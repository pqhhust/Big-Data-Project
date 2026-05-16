# BrainWatch — Week 1 Code Explanation

A walkthrough of every source module, script, test, and configuration file delivered in Week 1.

---

## 1. Project Configuration

### `pyproject.toml`

```toml
[project]
name = "brainwatch-big-data"
version = "0.2.0"
requires-python = ">=3.11"
dependencies = ["boto3>=1.34", "PyYAML>=6.0"]
```

- **Hatchling** build system — the package is installable via `pip install -e ".[dev]"`.
- `boto3` is needed for S3 access (BDSP data). `PyYAML` is for configuration parsing.
- Optional deps: `pytest` (dev), `kafka-python` (kafka), `pyspark` (spark).
- The `[tool.pytest.ini_options]` section adds `src/` to the Python path so tests can `import brainwatch` directly.

### `configs/project.example.yaml`

The single configuration file that governs all runtime behaviour:

```yaml
project_name: brainwatch
architecture: lambda

data_sources:
  eeg:
    source_reference_csvs:    # paths to 5 BDSP metadata CSVs
    local_raw_root: data/raw/eeg
    target_subset_hours: 8
  ehr:
    local_raw_root: data/raw/ehr
    bronze_root: data/bronze/ehr

kafka:
  bootstrap_servers: kafka:9092
  topics:
    eeg_raw: eeg.raw
    ehr_updates: ehr.updates
    realtime_features: features.realtime
    anomaly_alerts: alerts.anomaly

lakehouse:
  bronze_prefix: data/lake/bronze
  silver_prefix: data/lake/silver
  gold_prefix: data/lake/gold
```

Loaded at runtime by `config/settings.py` (see below).

### `src/brainwatch/config/settings.py`

```python
@dataclass(slots=True)
class ProjectSettings:
    project_name: str
    architecture: str
    raw: dict[str, Any]      # full parsed YAML dict

def load_settings(path: str | Path) -> ProjectSettings:
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    return ProjectSettings(
        project_name=payload["project_name"],
        architecture=payload["architecture"],
        raw=payload,
    )
```

- Uses `@dataclass(slots=True)` for memory efficiency.
- `raw` keeps the full YAML dict so any module can access nested config keys like `raw["kafka"]["topics"]["eeg_raw"]`.

---

## 2. Canonical Event Contracts

### `src/brainwatch/contracts/events.py`

This is the **data contract** layer — it defines the exact shape of every event that flows through the system. All producers, consumers, and Spark schemas must conform to these.

#### 4 Event Dataclasses

```python
@dataclass(slots=True)
class EEGChunkEvent:
    patient_id: str          # BIDS subject ID (e.g., "sub-S0001111189188")
    session_id: str          # BDSP session number
    event_time: str          # ISO-8601 timestamp
    site_id: str             # S0001, S0002, I0002, I0003, I0009
    channel_count: int       # typically 19 (10-20 montage)
    sampling_rate_hz: float  # typically 200 Hz (after resampling)
    window_seconds: float    # recording duration
    source_uri: str          # S3 key to the EDF file
```

- **EEGChunkEvent**: Represents one EEG recording session entering the system.
- **EHREvent**: One clinical event (admission, diagnosis, lab result, medication order). Contains a `payload: dict` for flexible structured data.
- **FeatureEvent**: Output of the speed layer — anomaly score + signal quality per time window.
- **AlertEvent**: Final alert with severity (critical/warning/normal/suppressed) and explanation.

#### Helper Functions

```python
def to_payload(record) -> dict:
    """Convert any event dataclass to a dict (for JSON serialisation)."""
    return asdict(record)

def validate_required_fields(payload: dict, required_fields: set) -> list[str]:
    """Return list of missing/empty required fields."""
    return [f for f in sorted(required_fields) if payload.get(f) in (None, "")]
```

- `to_payload()` uses `dataclasses.asdict()` — one-liner for Kafka serialisation.
- `validate_required_fields()` is used by producers and the bronze writer to catch invalid events before they enter the pipeline. Events that fail validation are routed to the dead-letter queue.

---

## 3. Ingestion Layer

### `src/brainwatch/ingestion/eeg_inventory.py`

**Purpose:** Profile all 306,741 metadata rows from 5 BDSP sites and produce a summary JSON.

#### Service-to-Task Mapping

```python
SERVICE_TASK_MAP = {
    "S0001": {"LTM": ["cEEG"], "EMU": ["cEEG"], "Routine": ["rEEG"], "OR": ["OR"]},
    "S0002": {"LTM": ["cEEG", "EEG"], "Routine": ["rEEG"], "EMU": ["EMU"], ...},
}
```

BDSP sites use different service names (LTM, Routine, EMU, etc.) for the same clinical tasks. This map normalises them so we can build correct BIDS-formatted S3 paths.

#### `build_candidate_s3_keys(row)`

```python
def build_candidate_s3_keys(row: dict) -> list[str]:
    site = row.get("SiteID") or row.get("InstituteID") or ""
    bids = row.get("BidsFolder") or ""
    session_id = row.get("SessionID") or ""
    # ...
    for task in resolve_task(site, service_name):
        keys.append(
            f"EEG/bids/{site}/{bids}/ses-{session_id}/eeg/"
            f"{bids}_ses-{session_id}_task-{task}_eeg.edf"
        )
    return keys
```

This constructs the full S3 key from metadata fields. Each row can produce 1–2 keys because some services map to multiple tasks (e.g., S0002 LTM → both `cEEG` and `EEG`).

**Example output:**
```
EEG/bids/S0001/sub-S0001111189188/ses-10/eeg/sub-S0001111189188_ses-10_task-cEEG_eeg.edf
```

#### `summarize_metadata(csv_paths, min_duration=30.0)`

The main profiling function. For every row in every CSV:
1. Parse duration (handles both `DurationInSeconds` and `RecordingDuration` column names — sites use different schemas).
2. Classify: missing duration → flag; below threshold → count as short; valid → accumulate hours.
3. Count by site, service, sex.
4. Track unique subjects via `BidsFolder` (BIDS subject ID).
5. Count candidate S3 keys.

Returns a dict with all statistics. The script `profile_eeg_metadata.py` calls this and writes it to `artifacts/week1/eeg_metadata_profile.json`.

#### Why this matters

Without this profiling step, we wouldn't know:
- How much data is actually available (3.27M hours)
- That 11,579 rows have no duration (data quality issue)
- That I0009 has only 7,626 rows (smallest site — affects stratification)
- That sites use different CSV column names (SiteID vs InstituteID, DurationInSeconds vs RecordingDuration)

---

### `src/brainwatch/ingestion/subset_manifest.py`

**Purpose:** Select a small, manageable subset for local development.

#### `select_subset(csv_paths, max_sessions=12, target_hours=8.0, min_duration=300, max_duration=5400)`

```python
candidates = []
for csv_path in csv_paths:
    for row in reader:
        duration = parse_duration(row)
        if duration is None or duration < min_duration or duration > max_duration:
            continue
        # ... build candidate record
        candidates.append({...})

# Sort shortest first, accumulate until limits reached
selected = []
for record in sorted(candidates, key=lambda r: r["duration_seconds"]):
    selected.append(record)
    accumulated_hours += record["duration_seconds"] / 3600.0
    if len(selected) >= max_sessions or accumulated_hours >= target_hours:
        break
```

**Key design decisions:**
- **Duration bounds (5–90 min):** Filters out ultra-short (<5 min, likely artifacts) and ultra-long (>90 min, too big for local staging).
- **Shortest first:** Maximises the number of distinct sessions within the target hours.
- **Two stopping conditions:** Either `max_sessions` or `target_hours`, whichever comes first.

Output: `artifacts/week1/eeg_subset_manifest.json` — 12 sessions, ~1 hour total, with BIDS S3 paths ready for download.

---

## 4. Processing Layer

### `src/brainwatch/processing/spark_pipeline.py`

**Purpose:** Week-1 skeleton demonstrating the Structured Streaming architecture. Not yet executed against a live Spark cluster — that comes in Week 2+.

#### Deferred Imports Pattern

```python
def build_realtime_query(spark, eeg_topic, ehr_topic, checkpoint_path, output_path):
    from pyspark.sql import functions as F
    from pyspark.sql.types import StructType, StructField, ...
```

All PySpark imports are **inside the function body**, not at the top of the module. This lets us:
- Import and test the module without PySpark installed.
- Run `pytest` on CI without a Spark cluster.
- Validate the code structure before Week 2 deployment.

#### EEG + EHR Schema Definitions

```python
eeg_schema = StructType([
    StructField("patient_id", StringType(), False),
    StructField("session_id", StringType(), False),
    StructField("event_time", TimestampType(), False),
    StructField("site_id", StringType(), False),
    StructField("channel_count", IntegerType(), True),
    StructField("sampling_rate_hz", DoubleType(), True),
    StructField("window_seconds", DoubleType(), True),
])
```

These mirror the `EEGChunkEvent` and `EHREvent` dataclasses from `contracts/events.py` — this is the Spark-side contract that must match the Kafka serialisation.

#### Stream-Stream Join with Watermarking

```python
eeg_stream = (
    spark.readStream.format("kafka")
    .option("subscribe", eeg_topic).load()
    .select(F.from_json(F.col("value").cast("string"), eeg_schema).alias("record"))
    .select("record.*")
    .withWatermark("event_time", "10 minutes")   # EEG tolerance: 10 min
)

ehr_stream = (
    spark.readStream.format("kafka")
    .option("subscribe", ehr_topic).load()
    .select(F.from_json(F.col("value").cast("string"), ehr_schema).alias("record"))
    .select("record.*")
    .withWatermark("event_time", "30 minutes")   # EHR tolerance: 30 min (delayed updates)
)
```

**Watermarking strategy:**
- EEG events arrive near real-time → 10-minute tolerance.
- EHR updates are often delayed (lab results, discharge summaries) → 30-minute tolerance.
- Spark uses these watermarks to know when it's safe to discard old state.

#### Join Logic

```python
joined = eeg_stream.join(
    ehr_stream,
    on=[
        eeg_stream.patient_id == ehr_stream.patient_id,
        ehr_stream.event_time <= eeg_stream.event_time,
        ehr_stream.event_time >= eeg_stream.event_time - F.expr("INTERVAL 30 MINUTES"),
    ],
    how="leftOuter",
)
```

- **`leftOuter` join:** Every EEG event is kept even if no matching EHR exists (most EEG windows won't have a simultaneous EHR update).
- **Time window:** Only join EHR events that occurred within 30 minutes before the EEG event.
- **Patient key:** `patient_id` links the two streams.

#### Windowed Aggregation + Anomaly Scoring

```python
features = (
    joined.groupBy(
        F.window(F.col("event_time"), "1 minute", "30 seconds"),  # tumbling window
        F.col("patient_id"),
        F.col("session_id"),
    )
    .agg(
        F.count("*").alias("eeg_chunk_count"),
        F.max("channel_count").alias("channel_count_max"),
        F.avg("sampling_rate_hz").alias("mean_sampling_rate_hz"),
        F.max(F.when(F.col("event_type") == "critical_lab", 1).otherwise(0))
         .alias("has_critical_lab"),
    )
    .withColumn(
        "anomaly_score",
        F.col("eeg_chunk_count") * 0.1 + F.col("has_critical_lab") * 0.5,
    )
)
```

- **1-minute windows with 30-second slide:** Generates features every 30 seconds for 1-minute rolling windows.
- **Anomaly score:** Simple weighted formula — more chunks = higher base score; critical lab result adds 0.5. This is a Week-1 placeholder; real feature engineering comes in Weeks 3-4.

#### `planned_spark_features()`

Documents all 10 required Spark techniques mapped to planned implementations:

```python
def planned_spark_features():
    return {
        "batch": ["bronze_to_silver_cleanup", "historical_eeg_ehr_join",
                   "gold_feature_rollups", "execution_plan_review"],
        "streaming": ["kafka_source_ingestion", "watermarking_and_late_data_handling",
                       "stateful_window_aggregations", "exactly_once_checkpointed_sinks"],
    }
```

---

## 5. Serving Layer

### `src/brainwatch/serving/anomaly_rules.py`

**Purpose:** Rule-based severity classification for anomaly alerts.

```python
@dataclass(slots=True)
class AlertDecision:
    severity: str       # "suppressed" | "critical" | "warning" | "normal"
    explanation: str

def classify_anomaly(anomaly_score: float, signal_quality_score: float) -> AlertDecision:
    if signal_quality_score < 0.3:
        return AlertDecision("suppressed", "Signal quality too low for a reliable alert.")
    if anomaly_score >= 0.85:
        return AlertDecision("critical", "Critical anomaly score with acceptable signal quality.")
    if anomaly_score >= 0.6:
        return AlertDecision("warning", "Elevated anomaly score requires review.")
    return AlertDecision("normal", "No alert threshold was crossed.")
```

**Decision logic (evaluated top to bottom):**

1. **Quality gate first:** If `signal_quality_score < 0.3`, suppress the alert regardless of anomaly score. This prevents false alarms from noisy/disconnected electrodes.
2. **Critical:** `anomaly_score >= 0.85` with acceptable quality → immediate clinician notification.
3. **Warning:** `0.6 <= anomaly_score < 0.85` → queue for review.
4. **Normal:** Below all thresholds → no action.

---

## 6. Infrastructure (Kubernetes)

### `infra/k8s/namespace.yaml`

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: brainwatch
```

Isolates all BrainWatch resources in their own Kubernetes namespace.

### `infra/k8s/configmap.yaml`

```yaml
data:
  KAFKA_BOOTSTRAP_SERVERS: "kafka-0.kafka.brainwatch.svc.cluster.local:9092"
  EEG_TOPIC: "eeg.raw"
  EHR_TOPIC: "ehr.updates"
  FEATURES_TOPIC: "features.realtime"
  ALERTS_TOPIC: "alerts.anomaly"
  BRONZE_PREFIX: "/data/lake/bronze"
  SILVER_PREFIX: "/data/lake/silver"
  GOLD_PREFIX: "/data/lake/gold"
```

Environment-specific overrides without code changes. Spark jobs and producers read topic names and paths from this ConfigMap via environment variables.

### `infra/k8s/spark-streaming-job.yaml`

A Kubernetes Job template that runs `spark-submit` with the Kafka connector package. In Week 1, this was a placeholder; it has since been updated for Week 2's bronze ingestion job.

---

## 7. Tests

### `tests/test_eeg_inventory.py`

```python
def test_summarize_metadata_counts_rows_and_candidate_keys(tmp_path):
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(
        "SiteID,BidsFolder,SessionID,DurationInSeconds,ServiceName,SexDSC\n"
        "S0001,sub-S0001AAA,1,120.0,LTM,Female\n"
        "S0002,sub-S0002BBB,2,20.0,LTM,Male\n"
    )
    summary = summarize_metadata([csv_path], min_duration=30.0)
    assert summary["total_rows"] == 2
    assert summary["unique_subjects"] == 2
    assert summary["total_candidate_s3_keys"] == 3     # S0001 LTM→1 key, S0002 LTM→2 keys
    assert summary["short_sessions_below_min_duration"] == 1  # 20s < 30s threshold
```

Creates a temporary CSV with 2 rows, then verifies:
- Row counting works
- Subject dedup works (2 different BidsFolders)
- S3 key generation respects service-to-task mapping (S0002 LTM produces 2 keys: cEEG + EEG)
- Short session detection flags the 20-second row

### `tests/test_subset_manifest.py`

```python
def test_select_subset_respects_max_sessions(tmp_path):
    # 3 candidates, all 3600s (1 hour each)
    records = select_subset([csv_path], max_sessions=2, target_hours=10)
    assert len(records) == 2
    assert records[0]["subject_id"] == "sub-1"   # sorted by duration → first
```

Verifies that `max_sessions=2` stops selection at 2 even though `target_hours=10` would allow more.

### `tests/test_anomaly_rules.py`

Three cases testing the classification logic:

```python
def test_low_quality_signal_suppresses_alert():
    decision = classify_anomaly(anomaly_score=0.95, signal_quality_score=0.2)
    assert decision.severity == "suppressed"
    # High anomaly score BUT low quality → suppressed (not critical)

def test_high_anomaly_score_becomes_critical():
    decision = classify_anomaly(anomaly_score=0.9, signal_quality_score=0.8)
    assert decision.severity == "critical"

def test_mid_anomaly_score_becomes_warning():
    decision = classify_anomaly(anomaly_score=0.7, signal_quality_score=0.8)
    assert decision.severity == "warning"
```

---

## 8. Generated Artifacts

### `artifacts/week1/eeg_metadata_profile.json`

Key numbers from profiling all 306,741 rows:

```json
{
  "total_rows": 306741,
  "unique_subjects": 115060,
  "total_candidate_s3_keys": 337466,
  "total_duration_hours_at_or_above_threshold": 3268943.95,
  "rows_by_site": {"I0002": 74488, "I0003": 61255, "I0009": 7626, "S0001": 96768, "S0002": 66604},
  "short_sessions_below_min_duration": 427,
  "rows_missing_duration": 11579
}
```

### `artifacts/week1/eeg_subset_manifest.json`

12 selected sessions for local development:

```json
{
  "record_count": 12,
  "estimated_total_hours": 1.02,
  "records": [
    {
      "site_id": "S0001",
      "subject_id": "sub-S0001...",
      "session_id": "...",
      "duration_seconds": 300,
      "candidate_source_keys": ["EEG/bids/S0001/.../file.edf"],
      "local_target_dir": "data/raw/eeg"
    },
    ...
  ]
}
```

---

## 9. How It All Fits Together

```
                    project.example.yaml
                          │
                          ▼
                    settings.py (load config)
                          │
          ┌───────────────┼────────────────┐
          ▼               ▼                ▼
   eeg_inventory.py  subset_manifest.py  events.py
   (profile 306k     (select 12          (define 4
    rows from CSVs)   sessions)           schemas)
          │               │                │
          ▼               ▼                │
   eeg_metadata_     eeg_subset_          │
   profile.json      manifest.json        │
                                          │
                          ┌───────────────┘
                          ▼
                   spark_pipeline.py
                   (streaming skeleton:
                    Kafka → watermark →
                    join → aggregate →
                    anomaly score)
                          │
                          ▼
                   anomaly_rules.py
                   (score → severity:
                    suppress/critical/
                    warning/normal)
                          │
                          ▼
                   K8s deployment
                   (namespace, ConfigMap,
                    Spark Job template)
```

**The contract flow:** `events.py` defines the schema → `eeg_inventory.py` produces metadata conforming to it → `spark_pipeline.py` uses matching Spark schemas → `anomaly_rules.py` classifies the output. Every module can be tested independently because the contracts are the interface.

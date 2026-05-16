# BrainWatch — Week 1 Q&A Preparation

Comprehensive question-and-answer guide for the Week 1 checkpoint.

---

## A. Problem Statement & Motivation

### Q1: What problem does BrainWatch solve?

**A:** Hospitals generate massive, continuous EEG streams (19–256 channels, 256–1024 Hz) that clinicians cannot monitor in real time. BrainWatch automatically detects neurological anomalies by combining EEG data with EHR context, generating sub-minute alerts for clinical decision support.

### Q2: Why is this a Big Data problem?

**A:** All four V's are present:

- **Volume:** 306,741 recordings across 5 clinical sites, 115,060 unique subjects, 3.2 million valid hours.
- **Velocity:** Continuous monitoring demands sub-minute ingestion-to-alert latency.
- **Variety:** EEG time-series (EDF files), structured EHR records (diagnoses, lab values, medications), metadata CSVs with different schemas per site.
- **Veracity:** 11,579 rows missing duration, 427 ultra-short sessions, missing patient IDs, schema differences across sites (`DurationInSeconds` vs `RecordingDuration`, `SiteID` vs `InstituteID`).

### Q3: Where does the data come from?

**A:** The BDSP (Brain Data Science Platform) clinical EEG corpus on AWS S3 — 5 hospital sites:

| Site | Records | Subjects |
|------|---------|----------|
| S0001 | 96,768 | ~35k |
| S0002 | 66,604 | ~25k |
| I0002 | 74,488 | ~28k |
| I0003 | 61,255 | ~22k |
| I0009 | 7,626 | ~5k |

Each recording is a BIDS-formatted EDF file on S3 with metadata CSVs for demographics, duration, service type.

### Q4: What is in scope vs out of scope?

**A:**
- **In scope:** EEG/EHR subset acquisition, Kafka stream simulation, Lambda Architecture (batch + speed + serving), Spark batch + Structured Streaming, Kubernetes deployment.
- **Out of scope:** Clinical-grade diagnosis, HIPAA compliance, production hospital deployment, foundation-model training.

---

## B. Architecture

### Q5: Why Lambda Architecture instead of Kappa?

**A:** Three reasons:

1. **Batch backfill:** We must reprocess historical EEG/EHR from S3 to build feature tables. Lambda has a native batch path; Kappa would replay everything through the stream.
2. **Course alignment:** We must demonstrate both Spark batch and Structured Streaming. Lambda naturally separates these.
3. **Different latency needs:** Historical computation tolerates minutes; real-time alerting needs seconds. Separate paths allow independent optimisation.

**Trade-off:** Higher operational complexity (two code paths), mitigated by sharing event contracts and Spark schemas across both.

### Q6: Explain the data flow.

**A:**
```
Raw EEG (S3) + EHR → Local staging → Kafka (eeg.raw, ehr.updates)
  → Batch: PySpark Bronze→Silver→Gold
  → Speed: Structured Streaming with watermarked joins
  → Anomaly scoring → Serving layer (Cassandra/MongoDB) → Dashboards
```

### Q7: What are Bronze, Silver, and Gold zones?

**A:**
- **Bronze:** Raw data as-ingested. Schema-validated only, no business logic. Immutable audit trail.
- **Silver:** Deduplicated, quality-flagged, patient-keyed. Version conflicts resolved.
- **Gold:** Joined EEG-EHR feature tables, alert summaries. Dashboard-ready.

### Q8: Why those specific Kafka topics?

**A:** Four topics for separation of concerns:

| Topic | Purpose |
|-------|---------|
| `eeg.raw` | Raw EEG events → consumed by Bronze ingest + Speed layer |
| `ehr.updates` | Raw EHR events → consumed by Bronze ingest + Speed layer |
| `features.realtime` | Speed-layer output → consumed by Serving layer |
| `alerts.anomaly` | Generated alerts → consumed by Dashboard/notifications |

Each consumer processes only relevant events and scales independently.

---

## C. Data Model & Contracts

### Q9: What are the canonical event schemas?

**A:** Four dataclasses in `contracts/events.py`:

1. **EEGChunkEvent** (8 fields): patient_id, session_id, event_time, site_id, channel_count, sampling_rate_hz, window_seconds, source_uri
2. **EHREvent** (7 fields): patient_id, encounter_id, event_time, event_type, source_system, version, payload (flexible dict)
3. **FeatureEvent** (6 fields): anomaly_score + signal_quality per window
4. **AlertEvent** (6 fields): severity + human-readable explanation

### Q10: Why `slots=True` on dataclasses?

**A:** Three benefits:
- ~30% less memory per instance (no per-instance `__dict__`)
- Faster attribute access
- Prevents accidental attribute creation (catches typos at runtime)

### Q11: How do you handle schema differences across sites?

**A:** Fallback chain pattern:
```python
duration = row.get("DurationInSeconds") or row.get("RecordingDuration") or ""
site = row.get("SiteID") or row.get("InstituteID") or ""
```
Handles heterogeneity without site-specific code paths.

### Q12: What is the service-to-task mapping?

**A:** BDSP sites label the same clinical tasks differently. We normalise:
```python
"S0001": {"LTM": ["cEEG"], "Routine": ["rEEG"], "EMU": ["cEEG"], "OR": ["OR"]}
"S0002": {"LTM": ["cEEG", "EEG"], "Routine": ["rEEG"], "EMU": ["EMU"]}
```
Note S0002 LTM maps to **two** tasks, producing multiple S3 key candidates per row. Unknown services default to `["EEG"]`.

---

## D. Implementation Details

### Q13: Walk me through metadata profiling.

**A:** `eeg_inventory.py` → `summarize_metadata()`:

1. Read each site CSV with `csv.DictReader`
2. Per row: parse duration (both column names), classify (missing/short/valid), count by site/service/sex, track unique subjects via `BidsFolder`, generate S3 keys
3. Return summary dict → written to `artifacts/week1/eeg_metadata_profile.json`

**Result:** 306,741 rows, 115,060 subjects, 337,466 S3 keys, 3.27M valid hours.

### Q14: How does subset selection work?

**A:** `subset_manifest.py` → `select_subset()`:

1. Filter by duration bounds (default 5–90 min)
2. Require valid BIDS S3 keys
3. **Sort by duration ascending** (shortest first)
4. Accumulate until `max_sessions` or `target_hours` reached

**Why shortest first?** Maximises unique subjects. 12 x 5-min sessions = 12 patients. 1 x 60-min session = 1 patient.

### Q15: Explain the Streaming skeleton.

**A:** `spark_pipeline.py` → `build_realtime_query()`:

1. **Kafka sources:** Parse JSON from `eeg.raw` and `ehr.updates` with explicit schemas
2. **Watermarking:** EEG 10 min (near-real-time), EHR 30 min (delayed updates)
3. **Stream-stream join:** `leftOuter` on `patient_id` + time window (30 min). Left-outer keeps all EEG even without matching EHR.
4. **Windowed aggregation:** 1-min window / 30-sec slide. Computes chunk count, max channels, mean sampling rate, critical lab flag.
5. **Anomaly score:** `chunk_count * 0.1 + critical_lab_flag * 0.5` (placeholder)
6. **Output:** Parquet, `update` mode, checkpointed

### Q16: Why defer PySpark imports?

**A:** Imports are inside function bodies, not at module top. This lets us `import brainwatch`, run pytest, and validate code without PySpark installed. Spark integration tests come in later weeks.

### Q17: How does anomaly classification work?

**A:** `anomaly_rules.py` → `classify_anomaly(score, quality)`:

1. If `quality < 0.3` → **suppressed** (unreliable signal, prevents false alarms)
2. If `score >= 0.85` → **critical** (immediate notification)
3. If `score >= 0.6` → **warning** (queue for review)
4. Else → **normal**

**Key:** Quality gate comes first. A high anomaly score from a bad signal is meaningless.

### Q18: What do `to_payload()` and `validate_required_fields()` do?

**A:**
- `to_payload()` → `dataclasses.asdict()` — converts event to dict for JSON/Kafka serialisation
- `validate_required_fields()` → returns list of missing/empty required fields. Failures go to dead-letter queue.

---

## E. Deep-Dive: Code Walkthrough

### Q19: What is the purpose of `eeg_inventory.py` and how is it structured?

**A:** It is the metadata profiling module. Its job is to answer: "How much data do we have, where is it, and what quality issues exist?" before we download or process anything.

Key functions:
- `resolve_task(site, service_name)` — Looks up `SERVICE_TASK_MAP` to normalise service names to BIDS task names. Returns `["EEG"]` as default for unknown combinations.
- `parse_duration(row)` — Extracts duration from either `DurationInSeconds` or `RecordingDuration` column. Returns `None` if missing or unparseable, which gets counted as a quality flag.
- `build_candidate_s3_keys(row)` — Constructs the full BIDS S3 path from site, subject, session, and task. One row can produce multiple keys (e.g., S0002 LTM → 2 tasks). This is critical because the S3 bucket uses a specific path convention: `EEG/bids/{site}/{bids_folder}/ses-{session_id}/eeg/{bids_folder}_ses-{session_id}_task-{task}_eeg.edf`.
- `summarize_metadata(csv_paths, min_duration)` — Main function. Iterates all rows from all CSVs, accumulates counters using `Counter` and `defaultdict`, and returns a summary dict with row counts, unique subjects, duration stats, quality flags, and sample S3 keys.
- `write_summary()` / `main()` — CLI glue: parse args, call `summarize_metadata`, write JSON output.

### Q20: What is `subset_manifest.py` doing differently from `eeg_inventory.py`?

**A:** While `eeg_inventory.py` **profiles all data** (analysis/reporting), `subset_manifest.py` **selects a small subset** for local development.

Key differences:
- Inventory scans everything and counts statistics; manifest filters and selects specific records.
- Manifest enforces stricter bounds (`min_duration=300s`, `max_duration=5400s`) to exclude both too-short and too-long recordings.
- Manifest produces actionable records with S3 keys and local target paths — ready for download.
- Manifest sorts by duration ascending and applies a greedy accumulation algorithm — this is the "shortest first" strategy for subject maximisation.

Both modules share `parse_duration()` and `build_candidate_s3_keys()` from `eeg_inventory.py` to avoid code duplication.

### Q21: Walk through `contracts/events.py` line by line — why these four schemas?

**A:** The four events represent the four stages of data in the pipeline:

1. **EEGChunkEvent** — Enters at the **ingestion** boundary. Represents "an EEG session arrived." Fields describe the recording: who (`patient_id`, `session_id`), where (`site_id`, `source_uri`), when (`event_time`), and what (`channel_count`, `sampling_rate_hz`, `window_seconds`).

2. **EHREvent** — Also enters at **ingestion**. Uses `payload: dict` instead of fixed fields because EHR events are heterogeneous — a lab result has different fields than a diagnosis or medication order. The `version` field supports idempotent updates (if the same encounter is re-sent with a higher version, the newer one wins).

3. **FeatureEvent** — Produced by the **speed layer** after joining EEG+EHR and computing aggregations. Contains the computed `anomaly_score` and `signal_quality_score` for a time window.

4. **AlertEvent** — Produced by the **serving layer** after applying `classify_anomaly()`. This is what clinicians see: a severity level and a human-readable explanation.

The helper `to_payload()` uses `dataclasses.asdict()` — a standard library one-liner that recursively converts nested dataclasses to dicts. `validate_required_fields()` is a guard at every pipeline boundary: producer → Kafka, Kafka → bronze writer, bronze → silver. It returns missing field names so error messages are actionable.

### Q22: Explain the Spark `eeg_schema` and `ehr_schema` — how do they relate to the Python dataclasses?

**A:** They are the **Spark-side mirror** of the Python dataclasses:

```
Python:  EEGChunkEvent.patient_id: str    →  Spark: StructField("patient_id", StringType(), False)
Python:  EEGChunkEvent.channel_count: int →  Spark: StructField("channel_count", IntegerType(), True)
```

- `False` (not nullable) on `patient_id`, `session_id`, `event_time` — these are required join keys
- `True` (nullable) on `channel_count`, `sampling_rate_hz` — optional metadata that may be missing

The schemas are used in `from_json()` to parse Kafka message values (raw JSON strings) into structured DataFrame columns. If the JSON doesn't match the schema, those rows get null values — which the bronze writer then routes to the dead-letter queue.

### Q23: Why does `spark_pipeline.py` have a separate `planned_spark_features()` function?

**A:** It's a documentation-as-code pattern. The function returns a dict mapping each required Spark technique to its planned implementation:

```python
{"batch": ["bronze_to_silver_cleanup", "historical_eeg_ehr_join", ...],
 "streaming": ["kafka_source_ingestion", "watermarking_and_late_data_handling", ...]}
```

This serves as a checklist that lives in the codebase (not a separate doc). When we implement each technique in Weeks 3-4, we can verify coverage by checking off items in this function. It also makes the course requirement mapping auditable.

### Q24: In `build_realtime_query()`, why `leftOuter` join instead of `inner`?

**A:** Because EEG events arrive continuously (every few seconds) but EHR events are sparse and asynchronous (a lab result might come once per hour). An `inner` join would **drop most EEG events** that don't have a coincident EHR update. `leftOuter` ensures every EEG event passes through with whatever EHR context is available (or null if none).

The join condition uses a 30-minute window:
```python
ehr_stream.event_time <= eeg_stream.event_time,
ehr_stream.event_time >= eeg_stream.event_time - F.expr("INTERVAL 30 MINUTES"),
```
This means: "attach any EHR event that occurred up to 30 minutes before this EEG chunk." Older EHR events are ignored (stale context); future EHR events haven't happened yet.

### Q25: Why `1 minute` window with `30 second` slide in the aggregation?

**A:** The window function `F.window("event_time", "1 minute", "30 seconds")` creates **overlapping sliding windows**:
- Window 1: 00:00–01:00
- Window 2: 00:30–01:30
- Window 3: 01:00–02:00
- ...

Each EEG event falls into **two** windows. This gives us smoother feature output (updated every 30 seconds) while capturing 1-minute context. The trade-off is 2x computation and storage, which is acceptable for our data volume.

If we used a tumbling window (no overlap), features would jump discontinuously at window boundaries. Sliding windows smooth this out — important for anomaly detection where gradual trends matter.

### Q26: Walk through the anomaly scoring formula: `chunk_count * 0.1 + critical_lab_flag * 0.5`.

**A:** This is a **Week 1 placeholder** — intentionally simple:

- `chunk_count * 0.1`: More EEG chunks in a 1-minute window = higher base score. If a patient has 10 chunks/minute (normal rate), that's a baseline score of 1.0. This captures "data volume" but not real signal features.
- `critical_lab_flag * 0.5`: If any EHR event with `event_type == "critical_lab"` matches this window, adds 0.5. This captures "clinical context says something is wrong."

Combined: a normal patient might score 1.0 (just EEG chunks), while a patient with a critical lab AND lots of EEG data might score 1.5+. The `classify_anomaly()` function then maps these scores to severity levels.

**In Week 4**, this will be replaced with real EEG-derived features (band power, entropy, spike detection) and more sophisticated EHR context weighting.

### Q27: Explain the `classify_anomaly()` decision order — why quality check first?

**A:** The order matters because of **short-circuit evaluation**:

```python
if signal_quality_score < 0.3:   # CHECK 1: quality gate
    return "suppressed"
if anomaly_score >= 0.85:        # CHECK 2: critical
    return "critical"
```

Consider a patient with `anomaly_score=0.95, signal_quality=0.1`:
- Without the quality gate first → **critical alert** → clinician rushes to bedside → finds a disconnected electrode → wasted time → alert fatigue
- With the quality gate first → **suppressed** → no false alarm → clinician trust preserved

This is a real clinical design pattern: EEG artifacts (muscle movement, electrode pop-off, 60Hz power line noise) can produce extreme signal values that naive anomaly detectors flag as critical. The quality gate prevents this.

### Q28: What is `settings.py` doing and why store `raw` as a field?

**A:** `settings.py` loads the YAML config into a `ProjectSettings` dataclass:

```python
@dataclass(slots=True)
class ProjectSettings:
    project_name: str      # "brainwatch"
    architecture: str      # "lambda"
    raw: dict[str, Any]    # full parsed YAML dict
```

The `raw` field keeps the entire YAML dict because different modules need different config sections:
- `ingestion/` needs `raw["data_sources"]["eeg"]["source_reference_csvs"]`
- `processing/` needs `raw["kafka"]["topics"]`
- `serving/` needs `raw["serving"]["nosql_backend"]`

Rather than creating a deeply nested dataclass hierarchy (which would be brittle and change often), we extract the two universal fields (`project_name`, `architecture`) and keep everything else accessible via `raw`. Modules look up what they need with `raw["kafka"]["topics"]["eeg_raw"]`.

---

## F. Deep-Dive: Testing

### Q29: What is the testing philosophy in Week 1?

**A:** Three principles:

1. **No external dependencies:** All tests use `tmp_path` (pytest fixture for temp directories) and inline CSV data. No Spark cluster, no Kafka, no S3, no real data files. This means tests run in <1 second anywhere.

2. **Test behaviour, not implementation:** Tests verify *what* functions produce (correct counts, correct classification), not *how* (which internal variables they use). This lets us refactor freely.

3. **Boundary-focused:** Tests cover the important decision points — duration thresholds, session limits, severity classification rules — where bugs would cause the most damage downstream.

### Q30: Walk through `test_summarize_metadata_counts_rows_and_candidate_keys` in detail.

**A:**
```python
csv_path.write_text(
    "SiteID,BidsFolder,SessionID,DurationInSeconds,ServiceName,SexDSC\n"
    "S0001,sub-S0001AAA,1,120.0,LTM,Female\n"    # valid, 120s > 30s threshold
    "S0002,sub-S0002BBB,2,20.0,LTM,Male\n"        # short, 20s < 30s threshold
)
summary = summarize_metadata([csv_path], min_duration=30.0)
```

This test validates **five things simultaneously** with just 2 rows:

1. **`total_rows == 2`** — Both rows are counted regardless of duration validity
2. **`unique_subjects == 2`** — Two different `BidsFolder` values = 2 subjects
3. **`total_candidate_s3_keys == 3`** — S0001 LTM → 1 key (cEEG), S0002 LTM → 2 keys (cEEG + EEG). This verifies the service-to-task mapping produces different key counts per site.
4. **`short_sessions_below_min_duration == 1`** — The 20s row is below the 30s threshold
5. **`rows_by_site["S0001"] == 1`** — Site-level counting works

**Why is assertion 3 the most important?** It catches a subtle bug: if the service-to-task mapping is wrong or missing, we'd generate wrong S3 paths and download the wrong files (or get 404s). The test proves that S0001 LTM → 1 key but S0002 LTM → 2 keys.

### Q31: Why does `test_select_subset_respects_max_sessions` use 3 candidates of equal duration?

**A:**
```python
"S0001,sub-1,1,3600,LTM\n"     # 1 hour
"S0001,sub-2,2,3600,Routine\n"  # 1 hour
"S0002,sub-3,3,3600,LTM\n"      # 1 hour

records = select_subset([csv_path], max_sessions=2, target_hours=10)
assert len(records) == 2
```

All three have the same duration (3600s). `target_hours=10` would allow all 3 (only 3 hours total). But `max_sessions=2` must stop at 2. This specifically tests that the **session count limit takes precedence** when hours haven't been exhausted.

The assertion `records[0]["subject_id"] == "sub-1"` verifies the sorting is stable (equal-duration records maintain insertion order).

### Q32: Walk through the three anomaly rule tests — what edge cases do they cover?

**A:**

**Test 1: `test_low_quality_signal_suppresses_alert`**
```python
classify_anomaly(anomaly_score=0.95, signal_quality_score=0.2)  # → "suppressed"
```
This is the **critical edge case**: score 0.95 would be "critical" without the quality gate. The test proves quality check runs before score check. If someone accidentally reorders the if-statements, this test fails.

**Test 2: `test_high_anomaly_score_becomes_critical`**
```python
classify_anomaly(anomaly_score=0.9, signal_quality_score=0.8)  # → "critical"
```
Score 0.9 (above 0.85 threshold) with quality 0.8 (above 0.3 gate) → critical. Tests the happy path for the most severe alert.

**Test 3: `test_mid_anomaly_score_becomes_warning`**
```python
classify_anomaly(anomaly_score=0.7, signal_quality_score=0.8)  # → "warning"
```
Score 0.7 is between 0.6 and 0.85 → warning. Quality 0.8 passes the gate. Tests the middle tier.

**What's NOT tested (and why):** The "normal" case (score < 0.6) is not explicitly tested because it's the fallback `return` — there's no conditional logic that could break. The three tests cover the three *decision boundaries* (quality gate, critical threshold, warning threshold) where bugs would be most impactful.

### Q33: Why use `tmp_path` instead of fixtures with real CSV files?

**A:** Four reasons:

1. **Self-contained:** The test file contains all data needed to run. No external file dependencies that could go missing, change format, or bloat the repo.
2. **Explicit:** A reviewer can see exactly what data the test operates on — 2 rows, specific values, known expected output.
3. **Fast:** Creating a temp file is microseconds. Reading a 96k-row real CSV would add seconds.
4. **Deterministic:** Real data changes over time (new rows added, corrections made). Test data is frozen. No flaky tests.

### Q34: The tests don't cover the CLI (`main()`) functions. Is that a gap?

**A:** Intentional for Week 1. The `main()` functions are thin wrappers:
```python
def main():
    args = build_parser().parse_args()
    summary = summarize_metadata(args.csv, min_duration=args.min_duration)
    write_summary(summary, args.output)
```

Testing `main()` would test argparse behaviour (Python standard library) and file I/O (already indirectly tested via `write_summary` in the profiling test). The core logic is in `summarize_metadata()` and `select_subset()`, which are tested. CLI integration tests (end-to-end) are planned for Week 5.

### Q35: How would you extend the test suite for Weeks 2-4?

**A:** Planned additions:

- **Week 2:** Tests for Kafka producer (using FileProducer fallback), EHR normaliser (schema validation, missing fields → None), bronze writer (dedup, partitioning, DLQ routing), dead-letter queue (route + read-back).
- **Week 3:** Spark batch tests using local SparkSession — test Bronze→Silver transformations, join correctness, window function output.
- **Week 4:** Streaming tests with `spark.readStream.format("rate")` (synthetic source) to validate watermarking, late-data handling, and checkpointing without Kafka.
- **Week 5:** End-to-end integration tests with Docker Compose (real Kafka + Spark).

---

## G. Infrastructure

### Q36: Explain the Kubernetes setup.

**A:** Three manifests:

1. **`namespace.yaml`** — `brainwatch` namespace for resource isolation
2. **`configmap.yaml`** — Topic names, lake prefixes, Kafka servers. Pods read via env vars. Same code across local/staging/prod by swapping ConfigMap.
3. **`spark-streaming-job.yaml`** — K8s Job running `spark-submit` with Kafka connector

### Q37: What monitoring is planned?

**A:**

| Metric | Alert Threshold |
|--------|----------------|
| Kafka consumer lag | > 10k messages |
| Micro-batch duration | > 60 seconds |
| Records in/out | Drop > 50% |
| Checkpoint freshness | Stale > 5 min |
| Alert rate by severity | Spike > 3 sigma |
| Failed records | > 1% of batch |

---

## H. Data Quality

### Q38: What data quality issues did you find?

**A:**
1. **Missing duration:** 11,579 rows (3.8%) — flagged and excluded from subsets
2. **Short sessions:** 427 rows < 30s — likely hookup artifacts
3. **Schema heterogeneity:** Different column names across sites
4. **Missing patient IDs:** Some S0001 rows lack `BDSPPatientID` — use `BidsFolder` instead
5. **Service ambiguity:** 143,369 rows (47%) "UNSPECIFIED" — I0002/I0009 don't populate this

### Q39: How are these handled?

**A:**
- Missing duration → counted, excluded from subset, preserved in profile
- Short sessions → configurable `min_duration` threshold
- Schema differences → fallback chain (`get("A") or get("B")`)
- Missing patient IDs → `BidsFolder` as canonical subject ID
- Unspecified services → default task `["EEG"]`

---

## I. Design Decisions

### Q40: Why NoSQL for serving?

**A:** (1) Write-heavy workload from continuous alerts, (2) Flexible schema for varying payloads, (3) Millisecond lookups by `patient_id`.

### Q41: Why a local subset?

**A:** Reproducibility, development speed (seconds vs hours), offline-capable, identical code paths as full dataset.

### Q42: What are the risks?

| Risk | Mitigation |
|------|-----------|
| No live EEG stream | Replay from manifests into Kafka |
| Spark cluster delays | Local-mode code + K8s templates ready |
| EHR schema issues | Canonical contract frozen in Week 1 |
| Inconsistent joins | Explicit alignment rules + quarantine |
| Scope creep | Prioritise end-to-end over modelling |

### Q43: What 10 Spark techniques?

| Technique | Week | Implementation |
|-----------|------|----------------|
| Window functions | 3 | Rolling patient metrics |
| Pivot / Unpivot | 3 | EHR lab features wide/long |
| Custom aggregation | 3 | Session-level quality scores |
| Multiple transformations | 3 | Bronze to Silver pipelines |
| UDFs | 4 | EEG feature logic, severity |
| Broadcast joins | 3 | Patient dimension lookups |
| Sort-merge joins | 3 | Large EEG-EHR joins |
| Optimization | 3 | Partition pruning, caching, explain |
| Streaming | 1+4 | Watermarks, checkpoints, output modes |
| Advanced analytics | 5 | Anomaly trends, optional MLlib |

---

## J. Team & Roadmap

### Q44: Role division?

| Role | Focus | Week 1 Output |
|------|-------|---------------|
| M1 Lead | Architecture, report | Decision doc, problem statement |
| M2 EEG | EEG pipeline, quality | Profiler, manifest builder |
| M3 EHR | EHR, joins | Event contracts, schema design |
| M4 Spark | Spark, Kafka | Streaming skeleton, technique map |
| M5 Infra | K8s, monitoring | Repo scaffold, manifests, tests |

### Q45: Week 1 done vs next?

**Week 1 (Complete):** Architecture doc, profiler (306k rows), manifest (12 sessions), 4 event contracts, Spark skeleton, anomaly rules, K8s manifests, config template, 5 tests, 2 artifacts.

**Week 2 (Next):** 100h EEG download, Kafka producers, EHR generation, bronze writer + DLQ, Streaming consumer, Docker Compose + K8s for Kafka.

**Weeks 3-6:** Batch layer, full speed layer, serving + dashboards, integration + report.

---

## K. Quick Reference Numbers

| Metric | Value |
|--------|-------|
| Total rows | 306,741 |
| Unique subjects | 115,060 |
| Sites | 5 |
| S3 keys | 337,466 |
| Valid hours | 3,268,944 |
| Missing duration | 11,579 (3.8%) |
| Short sessions | 427 |
| Week 1 subset | 12 sessions, ~1h |
| Week 1 tests | 5 |
| Kafka topics | 4 |
| EEG watermark | 10 min |
| EHR watermark | 30 min |
| Critical threshold | score >= 0.85 |
| Warning threshold | score >= 0.6 |
| Suppress threshold | quality < 0.3 |
| Window | 1 min / 30 sec slide |
| Join | leftOuter, patient_id +/- 30 min |
| Architecture | Lambda |
| Python | 3.11+ |
| Spark | 3.5 |

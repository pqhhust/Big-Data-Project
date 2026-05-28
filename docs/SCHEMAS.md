# BrainWatch storage schemas

This document is the single source of truth for **what columns live where** across the
medallion lake (bronze → silver → gold) and the Cassandra serving layer. Every schema is
linked back to the source-of-truth file in `src/brainwatch/` so the doc stays grounded.

Conventions used below:

- Types use a portable shorthand (`text`, `int`, `float`, `bool`, `timestamp`, `map`).
  Spark Parquet maps cleanly to `string / int / double / boolean / timestamp`;
  Cassandra uses the CQL spellings (`text / int / float / boolean / timestamp`).
- A trailing `?` marks a column that may be `null` because of upstream gaps
  (e.g. an EEG chunk with no matching EHR row).
- Partition / clustering columns are marked **pk** (primary / partition key) and
  **ck** (clustering key) where they apply.

---

## 1. Bronze — raw landing zone (HDFS JSONL)

**Layout on HDFS**

```
/lake/bronze/
├── eeg/
│   └── site=<id>/
│       └── date=<YYYY-MM-DD>/
│           └── eeg_bronze_<UTC-ts>.jsonl
└── ehr/
    └── date=<YYYY-MM-DD>/
        └── ehr_bronze_<UTC-ts>.jsonl
```

Writer: `src/brainwatch/ingestion/bronze_writer.py`. Mode = append-only.
Compression = none (plain text). Validation = required-field check; failures land in
`/lake/_dead_letter/date=<…>/dead_letter_<…>.jsonl` (see §5).

### 1.1 `bronze.eeg` (one JSON object per line)

Schema from `src/brainwatch/contracts/events.py::EEGChunkEvent`.

| Field | Type | Required | Notes |
|---|---|---|---|
| `patient_id` | text | ✅ | Subject identifier (BDSP-style ID, e.g. `I0002150000051`). |
| `session_id` | text | ✅ | EEG session this chunk belongs to. |
| `event_time` | text (ISO 8601) | ✅ | UTC timestamp of the chunk window-end. |
| `site_id` | text | ✅ | Site / facility ID. Becomes the bronze partition. |
| `channel_count` | int | — | Always `19` in the BDSP montage. |
| `sampling_rate_hz` | float | — | `200.0` for BDSP. Sanity-bounded `(0, 1000]` in silver. |
| `window_seconds` | float | — | Window length — defaults to `4.0`. |
| `source_uri` | text | — | Pointer to the EDF blob (e.g. `s3://bdsp-data/…`). |

The four `required` fields are enforced by `BronzeWriter._write()` (line 80). Missing
any of them routes the payload to the DLQ.

### 1.2 `bronze.ehr`

Schema from `src/brainwatch/contracts/events.py::EHREvent`.

| Field | Type | Required | Notes |
|---|---|---|---|
| `patient_id` | text | ✅ | Same key as in EEG. |
| `encounter_id` | text | ✅ | Hospital encounter ID. |
| `event_time` | text (ISO 8601) | ✅ | UTC timestamp of the EHR event. |
| `event_type` | text | ✅ | One of `critical_lab`, `medication`, `admission`, … |
| `source_system` | text | — | Originating EHR system tag. |
| `version` | int | — | Bronze keeps every version; silver picks the latest. |
| `payload` | map<text, any> | — | Free-form JSON object — opaque at bronze level. |

---

## 2. Silver — typed, deduplicated Parquet

**Layout on HDFS**

```
/lake/silver/
├── eeg/site_id=<id>/ingestion_date=<YYYY-MM-DD>/part-*.snappy.parquet
├── ehr/ingestion_date=<YYYY-MM-DD>/part-*.snappy.parquet
└── _dim/patient/part-*.snappy.parquet
```

Writer: `src/brainwatch/processing/silver_layer.py`. Mode = `overwrite`. Compression =
snappy. Each job is fully re-derivable from bronze.

### 2.1 `silver.eeg`

Source: `silver_layer.build_eeg_silver()` (lines 69-89).

Transformations:

1. `dropDuplicates(["patient_id", "session_id", "event_time"])` — cross-batch dedup.
2. Filter `sampling_rate_hz > 0 AND sampling_rate_hz <= 1000` — physical-plausibility.
3. Add `quality_flag`.
4. Add `ingestion_date = to_date(event_time)`.

| Column | Type | Source |
|---|---|---|
| `patient_id` | text | from bronze |
| `session_id` | text | from bronze |
| `event_time` | timestamp | from bronze (Spark parses the ISO string) |
| `site_id` | text | **partition column** (`partitionBy`) |
| `channel_count` | int | from bronze |
| `sampling_rate_hz` | float | from bronze (filtered to a plausible range) |
| `window_seconds` | float | from bronze |
| `source_uri` | text | from bronze |
| `quality_flag` | text | **new** — `LOW_SR` if `sampling_rate_hz < 100`, else `SHORT_WINDOW` if `window_seconds < 5`, else `OK` |
| `ingestion_date` | date | **new partition column** |

### 2.2 `silver.ehr`

Source: `silver_layer.build_ehr_silver()` (lines 92-113).

Transformation: latest version per `(patient_id, encounter_id)` via
`row_number() OVER (PARTITION BY patient_id, encounter_id ORDER BY version DESC)`.

| Column | Type | Source |
|---|---|---|
| `patient_id` | text | from bronze |
| `encounter_id` | text | from bronze |
| `event_time` | timestamp | from bronze |
| `event_type` | text | from bronze |
| `source_system` | text | from bronze |
| `version` | int | from bronze (only the max survives) |
| `payload` | struct | from bronze (Spark infers nested schema) |
| `ingestion_date` | date | **new partition column** |

### 2.3 `silver._dim/patient`

Source: `silver_layer.build_patient_dim()` (lines 116-135).

Distinct `patient_id` across EEG ∪ EHR, with a hashed key for join performance.

| Column | Type | Notes |
|---|---|---|
| `patient_id` | text | unique |
| `patient_key` | text | first 12 hex chars of `sha1(patient_id)` — stable, short, broadcast-friendly |

The directory contains a `_SUCCESS` marker (0 bytes) — a Hadoop convention that signals
the dim was fully written; consumers skip the dir if the marker is missing.

---

## 3. Gold — per-patient feature mart

**Layout on HDFS**

```
/lake/gold/
├── patient_features/event_date=<YYYY-MM-DD>/part-*.snappy.parquet
└── alert_summary/alert_date=<YYYY-MM-DD>/part-*.snappy.parquet    (optional)
```

Writer: `src/brainwatch/processing/gold_layer.py`. Mode = `overwrite`. One job per day.

### 3.1 `gold.patient_features`

Source: `gold_layer.build_patient_features()` (lines 13-60).

Build recipe:

1. `silver.eeg ⨝broadcast silver._dim/patient` on `patient_id` — adds `patient_key`.
2. `silver.eeg ⨝left-outer silver.ehr` on `patient_id` within
   `event_time ± 30 min` — windowed temporal join.
3. Group by `(patient_id, event_date)` and roll up.

| Column | Type | Source |
|---|---|---|
| `patient_id` | text | join key |
| `event_date` | date | **partition column** (`to_date(eeg.event_time)`) |
| `n_eeg_chunks` | bigint | `count(eeg.session_id)` |
| `mean_sampling_rate` | double | `avg(eeg.sampling_rate_hz)` |
| `has_critical_lab_today` | int (0/1) | `max(ehr.event_type == 'critical_lab')` |
| `n_medication_changes` | bigint | `sum(ehr.event_type == 'medication')` |

These four aggregates are exactly what the v2 anomaly score needs (`n_eeg_chunks` →
`chunk_term`, `has_critical_lab_today` → `critical_term`, `n_medication_changes` →
`meds_term`; the live `signal_quality_score` from the streaming path supplies
`quality_term`).

### 3.2 `gold.alert_summary` (optional)

Source: `gold_layer.build_alert_summary()` (lines 120-145). Written only if a JSONL
alerts export is supplied.

| Column | Type | Notes |
|---|---|---|
| `alert_date` | date | **partition column** |
| `severity` | text | one of `critical / warning / advisory / normal / suppressed` |
| `n_alerts` | bigint | count of alerts on `(alert_date, severity)` |

---

## 4. Cassandra — serving layer

Keyspace: `brainwatch`. Schema applied by
`src/brainwatch/serving/cassandra_sink.py::init_keyspace()` (lines 64-116).

### 4.1 `brainwatch.alerts`

Append-only time-series of alerts that fired. Designed for
"latest N alerts for patient X" reads from the dashboard.

| Column | CQL type | Key | Notes |
|---|---|---|---|
| `patient_id` | text | **pk** | Partition routed by this. |
| `alert_time` | timestamp | **ck** desc | Clustering — newest first. |
| `severity` | text | — | `critical / warning / advisory / normal / suppressed`. |
| `anomaly_score` | float | — | The single S ∈ [0, 1] from `compute_anomaly_score`. |
| `explanation` | text | — | Human-readable reason from `classify_v2`. |
| `session_id` | text | — | EEG session of the chunk that fired the alert. |
| `source` | text | — | `speed_lookup` or `speed_join` — which pipeline produced the row. |

```cql
PRIMARY KEY (patient_id, alert_time)
WITH CLUSTERING ORDER BY (alert_time DESC)
```

### 4.2 `brainwatch.patient_state`

One row per patient. Two role-owners write to disjoint column sets so they never
collide under Cassandra's upsert-by-default semantics.

| Column | CQL type | Role | Written by |
|---|---|---|---|
| `patient_id` | text **pk** | identity | both |
| `last_alert_time` | timestamp | Role A — last seen | `upsert_patient_state` (speed) |
| `last_severity` | text | Role A | speed |
| `signal_quality_score` | float | Role A | speed |
| `anomaly_score` | float | Role A | speed |
| `has_critical_lab` | bool | Role B — EHR enrichment | `upsert_patient_enrichment` (batch) |
| `n_medication_changes_24h` | int | Role B | batch |
| `enrichment_updated_at` | timestamp | Role B | batch |

Role A is the latest "what just happened" for the dashboard. Role B is the
Lambda-architecture **serving-store dimension**: the gold/batch path materializes EHR
enrichment per patient (`processing.gold_layer.materialize_patient_enrichment`), and
the speed layer reads it as a partition-key seek
(`cassandra_sink.fetch_patient_enrichment`) on every micro-batch — that's how the
streaming path scores on the full four-term v2 formula without a stream-stream join.

---

## 5. Quarantine — Dead-Letter Queue (DLQ)

**Layout on HDFS**

```
/lake/_dead_letter/date=<YYYY-MM-DD>/dead_letter_<YYYY-MM-DD>.jsonl
```

Writer: `src/brainwatch/ingestion/dead_letter.py::DeadLetterQueue.route()`.

Schema (one JSON object per line):

| Field | Type | Notes |
|---|---|---|
| `routed_at` | text (ISO 8601) | When the quarantine happened. |
| `reason` | text | Free-form, e.g. `"missing fields: site_id"`. |
| `original_payload` | object | Verbatim copy of the rejected record. |

The DLQ exists only at the bronze ingest boundary. Silver and gold do not have a DLQ
because by the time data reaches them it has already passed bronze validation — silver
**drops** physically-implausible rows (e.g. `sampling_rate_hz > 1000`) without
emitting a quarantine record, on the principle that bronze owns "we don't know what
this is" while silver owns "we know but it doesn't make sense and isn't worth
keeping."

---

## 6. Schema-change protocol

- **Bronze**: never rewrite; new optional fields are added by extending
  `contracts/events.py` and writing them through the existing dataclass. Producers
  must keep emitting the required four EEG / four EHR fields.
- **Silver**: schema is locked at job time. Adding a column means changing
  `silver_layer.py` and re-running the silver job against historical bronze
  (`mode("overwrite")` makes this cheap).
- **Gold**: same as silver — fully re-derivable from `(bronze + silver job)` and so
  free to evolve.
- **Cassandra**: `init_keyspace()` is idempotent and uses `ALTER TABLE … ADD <col>`
  inside `try/except` for backwards-compatible column additions (see
  `cassandra_sink.py:87-90, 108-116`). Dropping or renaming a column requires a
  manual `cqlsh` migration.

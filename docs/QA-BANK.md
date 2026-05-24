# BrainWatch — Consolidated Q&A Bank

Every question we've prepared an answer for, in one file, deduplicated and
re-organised. Pulled from:

- `docs/week1-qa.md` (Week-1 checkpoint Q&A — 45 questions)
- `docs/PRESENTATION-GUIDE.md` ("If they ask" defense boxes — 6 questions)
- `docs/STUDY-GUIDE.md` (top-10 defense rehearsal questions)
- New questions added here for completeness (deployment, cost, security, ethics)

> **Use this file as a study deck, not a script.** During the defense, answer
> in your own words; the wording below is just the *floor*. If you're short on
> time before stage time, jump to §0 ("the questions you absolutely will be
> asked") and §15 ("the killer questions").

---

## Table of contents

- [§0 The 10 questions you absolutely will be asked](#-0-the-10-questions-you-absolutely-will-be-asked)
- [§1 Problem statement & motivation](#-1-problem-statement--motivation)
- [§2 Architecture choices](#-2-architecture-choices)
- [§3 Data model & contracts](#-3-data-model--contracts)
- [§4 Ingestion & bronze](#-4-ingestion--bronze)
- [§5 Silver & gold (batch)](#-5-silver--gold-batch)
- [§6 Speed layer (streaming)](#-6-speed-layer-streaming)
- [§7 Serving & dashboard](#-7-serving--dashboard)
- [§8 Spark — the rubric depth questions](#-8-spark--the-rubric-depth-questions)
- [§9 Kafka & messaging](#-9-kafka--messaging)
- [§10 Cassandra & data modeling](#-10-cassandra--data-modeling)
- [§11 Kubernetes & EKS deployment](#-11-kubernetes--eks-deployment)
- [§12 Testing](#-12-testing)
- [§13 Data quality & honesty](#-13-data-quality--honesty)
- [§14 Cost, ops, security](#-14-cost-ops-security)
- [§15 The killer questions (advanced)](#-15-the-killer-questions-advanced)
- [§16 Team & roadmap](#-16-team--roadmap)
- [§17 Hybrid storage (HDFS + S3)](#-17-hybrid-storage-hdfs--s3)

---

## § 0 The 10 questions you absolutely will be asked

These are the ones every examiner reaches for first. Memorize the bolded line;
the rest is colour.

**1. Why Lambda and not Kappa?**
> **"Cheap historical reprocessing."** Re-running the batch path over an 8.5 GiB
> cold BDSP corpus is one Spark job. Kappa would mean re-streaming everything
> through Kafka, which is wasteful, slow, and forces us to retain those bytes
> in Kafka. Lambda also matches the course rubric — we have to demonstrate
> *both* Spark batch and Structured Streaming, and Lambda separates them naturally.

**2. What are Bronze / Silver / Gold?**
> **Raw / cleaned / business-ready.** Bronze = JSONL, immutable, append-only.
> Silver = Parquet+Snappy, deduplicated, `quality_flag`, partitioned by
> `site_id`/`ingestion_date`. Gold = per-patient daily features after a
> broadcast join with `patient_dim` and a ±30-minute EHR join.

**3. Walk me through the anomaly score.**
> **Weighted sum, clamped to 0..1.**
> `0.30·chunk_term + 0.25·quality_term + 0.30·critical_term + 0.15·meds_term`.
> v1 has a quality gate first: `signal_quality < 0.3 → suppressed`.
> v2 thresholds: 0.40 advisory, 0.65 warning, 0.85 critical, plus a fast
> critical-lab path at 0.60. Weights are load-bearing — we don't drift them.

**4. How does the watermark bound state in Structured Streaming?**
> **"Watermark = max event-time − lateness."** In the speed layer we set
> `withWatermark("event_time", "30 seconds")`. Spark evicts window state older
> than the watermark, so memory stays bounded. Late events past the watermark
> are dropped (not joined). This is how we can run forever without OOM.

**5. Why broadcast join in gold?**
> **"Pay the broadcast so you don't pay the shuffle."** `patient_dim` is small
> (one row per patient). Broadcasting it to every executor lets every partition
> of EEG/EHR do the lookup locally → no shuffle. Asserted in a test
> (`tests/test_gold_layer.py`).

**6. How is exactly-once handled in the speed layer?**
> Three legs in classic Structured Streaming style: **replayable source** (Kafka
> offsets stored in the checkpoint), **idempotent sink** (Cassandra
> `PRIMARY KEY (patient_id, alert_time)` → re-insert is a no-op upsert), and
> **checkpointed state** (Spark checkpoint dir on a PVC). Restart-safe.

**7. Why Cassandra and not Postgres for alerts?**
> Write-heavy, append-mostly, trivially partitionable by `patient_id`. Cassandra
> is **masterless** (every node is a coordinator → linear write scaling) and
> tunable-consistency (we use `LOCAL_ONE` on writes). Postgres would be a
> primary-replica bottleneck on writes. Reads by `patient_id` are O(1) — that
> single partition is local.

**8. Why doesn't the EDF binary go through Kafka?**
> EDF files are **10s–100s of MB each**. You never push that volume through a
> message bus; the binary stays in the data lake, and events carry a
> `source_uri` reference plus **measured features** (`signal_quality_score`,
> `mean_amplitude_uv`, `flat_channel_frac`, `clipping_frac`). Standard pattern.

**9. What did you actually deploy on AWS?**
> An EKS cluster (2× m5.xlarge), Kafka 3.9 in KRaft StatefulSet, Cassandra 4.1
> StatefulSet, four real-pipeline Deployments (`kafka-producer`, `speed-layer`,
> `cassandra-schema-init` Job, `cassandra-exporter`), Grafana NodePort, plus
> EBS gp3 PVCs and an S3 static-website bucket for the dashboard. The static
> dashboard survives the cluster being torn down — that's why we can demo at
> ~$1/month.

**10. What did you learn?**
> Use `docs/final-report.md` §"Lessons Learned" — 11 categories aligned to the
> rubric. One sentence each is enough: schema heterogeneity, watermarks, the
> stream-stream join limit (we abandoned it for append-mode + windowed agg),
> the cost of NAT gateways, that checkpoints are *not* portable across schema
> changes, that BOM bytes in the AWS root-key CSV are real, that snapshots
> let you pause an EKS cluster for ~$1/mo, that broadcast joins matter more
> than people think, that `dropDuplicates` needs the right key (we had a bug
> there), that `hash()` is salted per-process (use `zlib.crc32`), and that
> Grafana via S3 + Infinity is the most resilient dashboard pattern.

---

## § 1 Problem statement & motivation

### Q1.1 What problem does BrainWatch solve?
Hospitals generate continuous EEG streams (19–256 channels, 256–1024 Hz) that
clinicians cannot monitor in real time. BrainWatch automatically detects
anomalies by combining EEG signal features with EHR clinical context,
generating sub-minute alerts.

### Q1.2 Why is this a Big Data problem? (the four V's)
- **Volume:** 306,741 recordings, 115,060 subjects, 3.2M valid hours across 5
  sites in the full BDSP corpus.
- **Velocity:** continuous monitoring; sub-minute ingestion-to-alert.
- **Variety:** EDF time-series + structured EHR + per-site CSV metadata with
  schema differences (`DurationInSeconds` vs `RecordingDuration`).
- **Veracity:** 11,579 rows missing duration (3.8%), 427 ultra-short sessions,
  missing patient IDs in some sites.

### Q1.3 Where does the data come from?
The **BDSP** (Brain Data Science Platform, Harvard/MGH) credentialed EEG corpus
on AWS S3. 5 hospital sites; our cohort uses 4 (S0001, S0002, I0002, I0003).
EDF binary + per-site CSV metadata.

### Q1.4 What is in scope vs out of scope?
**In:** subset acquisition, Kafka simulation, Lambda architecture, Spark batch
+ Streaming, Cassandra serving, K8s/EKS deployment, Grafana.
**Out:** clinical-grade diagnosis, HIPAA compliance, production hospital
deployment, foundation-model training.

### Q1.5 Is this real medical data?
Yes — the **EDF waveforms are real BDSP/Harvard recordings** via the
credentialed access point. **The ICD-10 diagnoses are real HEEDB neurology
codes.** What is *synthetic* and we say so explicitly: the EHR vitals/labs
timestamps and the anomaly-score-variance term. Honesty about this is in
`docs/final-report.md`.

---

## § 2 Architecture choices

### Q2.1 Why Lambda not Kappa?
See §0 Q1. Three concrete reasons: cheap reprocessing, course requires both
batch + streaming, different latency budgets per path.

### Q2.2 What's the trade-off of Lambda?
**Operational complexity** — two code paths. We mitigate by sharing the event
contracts (`contracts/events.py`) and Spark schemas across batch and streaming.

### Q2.3 Why Bronze JSONL but Silver/Gold Parquet?
- **JSONL bronze** is append-only, human-readable, line-splittable. Failures
  at the landing edge are easy to debug.
- **Parquet+Snappy silver/gold** is ~42× smaller, columnar (predicate/column
  pushdown), splittable for parallel reads.

### Q2.4 Why a NoSQL serving store?
Three reasons: write-heavy (continuous alerts), flexible schema (varying alert
payloads), millisecond `patient_id` lookups.

### Q2.5 Why Grafana with the Infinity datasource over a custom React dashboard?
The React dashboard was the first cut; we replaced it with Grafana because
(a) Grafana has built-in time-series panels, alerting, and templating; (b) the
**Infinity datasource lets Grafana read JSON from any URL** — we point it at
the S3 static-website bucket. That means the dashboard survives the EKS
cluster being deleted. The original React app is preserved in `dashboard/` for
the explorer/notes shell.

### Q2.6 Draw the data flow.
See `docs/STUDY-GUIDE.md` §4 or `docs/PRESENTATION-GUIDE.md` §2. Top-to-bottom:
BDSP → download_real_edf → bronze JSONL → (split) batch silver/gold AND speed
layer Kafka→Spark→Cassandra → exporter → S3 → Grafana.

---

## § 3 Data model & contracts

### Q3.1 What are the canonical event schemas?
Four dataclasses in `src/brainwatch/contracts/events.py`:
1. **`EEGChunkEvent`** — patient_id, session_id, event_time, site_id,
   channel_count, sampling_rate_hz, window_seconds, source_uri.
2. **`EHREvent`** — patient_id, encounter_id, event_time, event_type,
   source_system, version, **payload (flexible dict)**.
3. **`FeatureEvent`** — anomaly_score + signal_quality per window.
4. **`AlertEvent`** — severity + human-readable explanation.

### Q3.2 Why `slots=True` on the dataclasses?
~30% less memory per instance (no per-instance `__dict__`), faster attribute
access, prevents typo-driven attribute creation at runtime.

### Q3.3 How do you handle schema differences across sites?
Fallback chain pattern:
```python
duration = row.get("DurationInSeconds") or row.get("RecordingDuration") or ""
site = row.get("SiteID") or row.get("InstituteID") or ""
```

### Q3.4 What is the service-to-task mapping?
BDSP sites label the same clinical task differently. We normalise via
`SERVICE_TASK_MAP` in `eeg_inventory.py`. Note S0002 LTM → **two** tasks
(`cEEG` + `EEG`), so the same row can produce multiple S3 key candidates.
Unknown service → `["EEG"]` default.

### Q3.5 How do the Python dataclasses relate to Spark schemas?
The Spark `StructType` is a 1-to-1 mirror with explicit nullability:
- `False` (required) for join keys: `patient_id`, `session_id`, `event_time`.
- `True` for optional metadata: `channel_count`, `sampling_rate_hz`.
Used in `from_json()` to parse Kafka values into structured columns;
non-matching rows produce nulls → bronze writer routes them to the DLQ.

### Q3.6 Why store the full YAML as `raw: dict` in `ProjectSettings`?
Different modules need different config sections (`raw["kafka"]["topics"]`,
`raw["data_sources"]["eeg"]`...). A deeply-nested dataclass hierarchy would be
brittle. We extract the two universal fields (`project_name`, `architecture`)
and let everything else flow through `raw`.

---

## § 4 Ingestion & bronze

### Q4.1 Walk through metadata profiling.
`eeg_inventory.summarize_metadata()`:
1. Reads each site CSV with `csv.DictReader`.
2. Per row: parses duration (both column names), classifies, counts by
   site/service/sex, tracks unique subjects via `BidsFolder`, generates S3 keys.
3. Returns a summary dict written to
   `artifacts/week1/eeg_metadata_profile.json`.
Result: 306,741 rows · 115,060 subjects · 337,466 S3 keys · 3.27M valid hours.

### Q4.2 How does subset selection work? Why shortest-first?
`subset_manifest.select_subset()`: filter by duration, require valid S3 keys,
**sort ascending by duration**, greedy accumulate until `max_sessions` or
`target_hours`. Shortest-first **maximises unique subjects**:
12 × 5-min sessions = 12 patients; 1 × 60-min session = 1 patient.

### Q4.3 What does the bronze writer guarantee?
1. **SHA-256 dedup** by event fingerprint — re-running ingestion is idempotent.
2. **DLQ routing** — `validate_required_fields()` failures land in
   `data/lake/_dead_letter/<date>.jsonl`.
3. **Partitioning** — by `site_id`, `ingestion_date`.

### Q4.4 Why measured signal-quality features in bronze and not silver?
We compute `signal_quality_score`, `mean_amplitude_uv`, `flat_channel_frac`,
`clipping_frac` in `edf_to_bronze.py` because we need the **raw EDF** open
anyway. Once we land bronze JSONL, the EDF is referenced only by `source_uri`
and the per-window features are sufficient downstream.

### Q4.5 Why the BOM-stripping `encoding="utf-8-sig"` on the AWS key CSV?
Real bug: AWS-exported `rootkey.csv` starts with a UTF-8 BOM. Default
`csv.DictReader` keeps the BOM in the first column name → `KeyError`.
`utf-8-sig` strips it. (Logged as a real lesson learned.)

---

## § 5 Silver & gold (batch)

### Q5.1 What does the silver layer actually do?
`processing/silver_layer.py`:
- `_read_bronze` **sniffs format** (JSONL vs Parquet) via `os.walk` — handles
  both our JSONL bronze and any earlier Parquet bronze.
- `build_eeg_silver`: `dropDuplicates(["patient_id","session_id","event_time"])`,
  adds `quality_flag` ∈ {`OK`, `LOW_SR`, `SHORT_WINDOW`}, writes
  `partitionBy("site_id","ingestion_date")`.
- `build_ehr_silver`: `row_number().over(partitionBy(patient_id, encounter_id)
  .orderBy(version desc))` keeps the latest version of each EHR event.
- `build_patient_dim`: SCD-style key via `sha1` of the patient identifier.

### Q5.2 What does the gold layer compute?
`processing/gold_layer.py`:
- `build_patient_features`: `F.broadcast(patient_dim)` join, then a ±30-minute
  EHR join, then per `patient_id × event_date` rollups: `n_eeg_chunks`,
  `mean_sampling_rate`, `has_critical_lab_today`, `n_medication_changes`.
- `build_alert_summary`: severity breakdown of the alerts dataset.

### Q5.3 Why ±30 minutes for the EHR join?
Clinical context that's more than 30 minutes old is **stale** for a real-time
alert. EHR events newer than the EEG haven't happened yet (causality). The
30-minute window balances completeness against staleness; matches the EHR
watermark.

### Q5.4 What output mode does the batch write use?
`overwrite` for silver/gold rebuilds (idempotent), partitioned writes for
incremental ingestion days.

---

## § 6 Speed layer (streaming)

### Q6.1 What's the streaming pipeline?
`processing/speed_layer.build_kafka_streaming_pipeline`:
1. `spark.readStream.format("kafka")` from `eeg.raw`.
2. `from_json()` with explicit `eeg_schema`.
3. `withWatermark("event_time", "30 seconds")`.
4. Windowed aggregation: `F.window(event_time, "30 seconds", "15 seconds")` +
   `count/avg/max`.
5. UDF wraps `compute_anomaly_score` + `classify_v2`.
6. `foreachBatch` writes to Cassandra.
7. Output mode: **append**, checkpointed on a PVC.

### Q6.2 Why append mode and not update?
Spark **forbids stream-stream join in update mode** with windowed aggregation.
We tried update + stream-stream EHR join; the engine rejected it. We switched
to append, dropped the EHR join from the live path, and **moved EHR enrichment
to the batch/gold join**. This is a real lesson learned, documented.

### Q6.3 What's the windowed aggregation window/slide and why?
**30 s window, 15 s slide.** Each event falls into two overlapping windows →
smoother feature output every 15 s while capturing 30 s of context. A tumbling
window would jump discontinuously at boundaries; sliding smooths trends —
important for anomaly detection.

### Q6.4 How is `foreachBatch` used?
For each micro-batch DataFrame, we:
1. Open a `cassandra-driver` session in a `try`/`finally` (cluster leaks were
   a real bug).
2. Prepare a batched `INSERT INTO alerts (...) VALUES (...)`.
3. Execute per-row. Cassandra PK upsert → idempotent.

### Q6.5 Why `zlib.crc32` instead of Python's `hash()`?
Python's `hash()` is **salted per process** (PYTHONHASHSEED is randomised by
default), so the same string can hash differently across pod restarts → state
inconsistency between checkpoint and restart. `zlib.crc32` is deterministic.

### Q6.6 What's in the checkpoint dir?
1. **Kafka offsets** consumed per batch.
2. **State store** for windowed aggregations.
3. **Source progress** (current offsets per source).
4. **Commit log** of completed batches.
Stored on `checkpoints-pvc` (EBS gp3). Deleting the dir = lose progress.

### Q6.7 What happens if the speed-layer pod restarts mid-batch?
1. Pod restarts; container re-`pip install`s the wheel; `spark-submit`
   re-runs.
2. Spark reads the checkpoint, resumes from the last committed Kafka offsets.
3. Any in-flight batch is replayed; Cassandra PK upsert makes the write
   idempotent. Net effect: at-least-once on the wire, **exactly-once visible
   in Cassandra**.

---

## § 7 Serving & dashboard

### Q7.1 How does Grafana read the data without hitting Cassandra directly?
`scripts/cassandra_to_s3_exporter.py` runs in a pod. Every 3 s it:
1. Queries `SELECT * FROM brainwatch.alerts LIMIT 5000`.
2. Builds the canonical rollups via `analytics.rollups` (summary, severity
   breakdown, timeline, score histogram, top patients, recent).
3. Uploads each as JSON to the S3 static-website bucket with
   `CacheControl: no-cache,max-age=0`.

Grafana points the **Infinity datasource** at those URLs and renders the panels.

### Q7.2 Why JSON-via-S3 instead of a Cassandra Grafana plugin?
Three reasons: (a) **decouples Grafana from Cassandra** — dashboard works with
the cluster deleted; (b) S3 is essentially free for our volume; (c) the
rollups are *exactly* the analytics our dashboards need — no Cassandra
secondary indexes or large `SELECT`s.

### Q7.3 What are the 6 dashboards?
1. **Live Alerts** (`grafana-dashboard.json`) — severity timeline, recent
   alerts, score histogram, top patients.
2. **Pipeline & Infra** (`grafana-pipeline-dashboard.json`) — events through
   each layer, batch runtime, compression ratios. **Now live-fed** by
   `cluster-state-exporter` (was static at v1).
3. **Clinical Insights** (`grafana-insights-dashboard.json`) — real ICD-10
   prevalence, per-site breakdown, diurnal pattern.
4. **Data Explorer** (`grafana-explorer-dashboard.json`) — sample rows + counts
   at bronze/silver/gold + a notes panel.
5. **About** (`grafana-about-dashboard.json`) — architecture, links, team.
6. **Architecture Status** (`grafana-cluster-status-dashboard.json`) —
   live nodes, pods by app, HDFS health, CronJob schedule + last fire,
   streamer progress, alert count. Refreshes every 30 s from
   `cluster_*.json` written by the `cluster-state-exporter` pod.

### Q7.4 Why does the diurnal panel use a bar chart and not a timeseries?
Hour-of-day (0..23) is **categorical**, not a time field. Grafana's timeseries
panel needs a real time axis; using it on hour-of-day produced "Data is missing
a time field." Bar chart is the right primitive.

### Q7.5 How do you handle the dashboard's "No data" panels?
Common culprits: (a) the Infinity field's `text` doesn't match the panel's
`reduceOptions` field regex; (b) a `displayName` override renames the field
out from under the regex. Fix is to align selectors with field names (see
`grafana-pipeline-dashboard.json` — committed fix).

---

## § 8 Spark — the rubric depth questions

### Q8.1 Which 10 Spark techniques does the project demonstrate?

| Technique | File | What it does |
|---|---|---|
| Window functions | `silver_layer.build_ehr_silver` | `row_number()` over `(patient_id, encounter_id)` ordered by `version desc` |
| Pivot / unpivot | analytics scripts | EHR labs wide↔long |
| Custom aggregation | gold rollups | per-patient daily features |
| Multiple transforms | bronze→silver→gold | the medallion path |
| UDFs | speed layer | `compute_anomaly_score` |
| Broadcast joins | `gold_layer` | `F.broadcast(patient_dim)` |
| Sort-merge joins | `gold_layer` | large EEG⋈EHR |
| Partition pruning | silver write | `partitionBy("site_id","ingestion_date")` |
| Structured Streaming | speed layer | watermark, window, append |
| MLlib | `train_severity_model.py` | LogisticRegression + AUC |

### Q8.2 What is the Catalyst optimizer doing for us?
Catalyst rewrites the logical plan: predicate pushdown into Parquet, column
pruning, constant folding, broadcast-vs-sort-merge selection (size-based),
join reordering. We don't write any of that — `df.explain()` shows the result.

### Q8.3 Where do you use `.cache()` or `.persist()`?
Sparingly. In `gold_layer`, we cache the small `patient_dim` after enrichment
because it's joined twice. **General rule:** cache only after a DataFrame is
reused; otherwise it's pure cost.

### Q8.4 What's a sort-merge join and when does Spark pick it?
Default for two large DataFrames. Spark sorts both sides on the join key
(stage boundary), then merges. We pick **broadcast** instead when one side
fits in driver memory (configurable; ~10 MB default).

### Q8.5 What happens if `spark.sql.shuffle.partitions` is too low?
Each shuffle partition becomes large → OOM on executors, or huge skew. Too
high → many tiny tasks, scheduler overhead. We use **256** for the 8 GiB
batch (real bug: default 200 OOM'd on a heavy join — we bumped driver to 24g
and partitions to 256).

### Q8.6 Why `master('local[4]')` in tests?
The Spark tests use a local SparkSession with 4 threads. No cluster needed,
fast (< 1s per test once the JVM is warm), CI-friendly.

---

## § 9 Kafka & messaging

### Q9.1 What are the topics?
| Topic | Producer | Consumer |
|---|---|---|
| `eeg.raw` | `kafka_producer_driver` | speed layer, bronze ingest |
| `ehr.updates` | EHR loader | speed layer, bronze ingest |
| `features.realtime` | speed layer | serving |
| `alerts.anomaly` | serving | dashboard / notifications |

### Q9.2 Why KRaft mode?
**No ZooKeeper.** Apache deprecated ZK in 3.5+. One fewer stateful service to
run, simpler EKS manifest, simpler failure modes.

### Q9.3 How is the producer rate set?
`scripts/kafka_producer_driver.py --rate 150` → 150 events/second across the
real cohort. We chose 150 because it gives a visible alert rate on the
dashboard (~10/min after the score filter) without saturating the EKS workers.

### Q9.4 What's the partition key?
`patient_id` for `eeg.raw` and `ehr.updates`. Ordering per-patient is preserved
across consumer restarts; consumers can scale by partition count.

### Q9.5 What's Kafka's role in resilience?
Replayable commit log: if the speed layer dies, we resume from the last
committed offset. We retain 24 hours (configurable). Producer back-pressure is
implicit: if Kafka is slow, the producer's send buffer fills and the producer
blocks.

---

## § 10 Cassandra & data modeling

### Q10.1 Show me the schema.
```sql
CREATE KEYSPACE brainwatch WITH replication = {'class':'SimpleStrategy','replication_factor':1};

CREATE TABLE brainwatch.alerts (
  patient_id     text,
  alert_time     timestamp,
  severity       text,
  anomaly_score  float,
  explanation    text,
  PRIMARY KEY (patient_id, alert_time)
) WITH CLUSTERING ORDER BY (alert_time DESC);

CREATE TABLE brainwatch.patient_state (
  patient_id         text PRIMARY KEY,
  last_alert_time    timestamp,
  last_severity      text,
  last_anomaly_score float
);
```

### Q10.2 Why partition by `patient_id`?
Most natural query: "show me this patient's alerts." Partition key
collocates them on one node → no scatter-gather. The trade-off is that a
single hot patient could hot-spot a node; we accept this because alert volume
per patient is bounded.

### Q10.3 Why clustering `DESC`?
"Newest alerts first" without `ORDER BY` — on-disk order *is* the answer.

### Q10.4 What's the consistency level?
`LOCAL_ONE` on writes for throughput. `LOCAL_QUORUM` on reads in the exporter
(we'd want QUORUM in production; SimpleStrategy `RF=1` makes this moot in our
demo).

### Q10.5 What's `patient_state` used for?
Latest-state lookups — "is this patient currently in a critical episode?" The
exporter updates it with a `last-write-wins` upsert per alert.

### Q10.6 CAP — where does Cassandra sit?
**AP.** We tolerate transient inconsistency for write availability. For alerts
this is the right call: a brief blink where one replica is behind is better
than refusing writes. The batch layer reconciles long-term.

---

## § 11 Kubernetes & EKS deployment

### Q11.1 What objects make up the cluster?
- **Namespace** `brainwatch`.
- **ConfigMap** with topics, paths, broker addresses.
- **Secret** `aws-credentials` (access-key-id + secret-access-key).
- **StatefulSets:** `kafka`, `cassandra` (stable identity + per-pod PVC).
- **Deployments:** `kafka-producer`, `speed-layer`, `cassandra-exporter`, `grafana`.
- **Job:** `cassandra-schema-init`.
- **Services:** `kafka` (headless), `cassandra-svc` (headless), `grafana` (NodePort).
- **PVCs:** `bronze-pvc`, `checkpoints-pvc`, `kafka-data-kafka-0`,
  `cassandra-data-cassandra-0`, `grafana-data`.

### Q11.2 Why a StatefulSet for Kafka/Cassandra and not a Deployment?
StatefulSets give each pod a **stable name and a stable PVC**. Kafka and
Cassandra need stable identities so they can find each other on restart and
their EBS volume reattaches to the same pod ordinal.

### Q11.3 Why the busybox initContainer on Kafka?
EBS gp3 volumes ship with a `lost+found` directory owned by root. Kafka 3.9
in KRaft refuses to start if there are unknown files in the data dir.
InitContainer removes `lost+found` and chowns the dir to Kafka's UID before
the main container starts.

### Q11.4 Walk me through `real-pipeline.yaml`.
Five things, in order:
1. **`cassandra-schema-init` Job** — waits for Cassandra, applies the CQL.
2. **`kafka-producer` Deployment** — init pulls the wheel from S3, main runs
   `kafka_producer_driver.py --rate 150`.
3. **`speed-layer` Deployment** — `spark:3.5.5-...-python3-ubuntu` image,
   `pip --target=/code/site-packages`, `PYTHONPATH=…`, `spark-submit` with the
   Kafka package.
4. **`cassandra-exporter` Deployment** — installs wheel (the rollup refactor
   dep), runs `cassandra_to_s3_exporter.py`.
5. (Out of file) Grafana Deployment + NodePort.

### Q11.5 Why `pip --target=/code/site-packages` and `PYTHONPATH`?
The Spark image's `/home/spark` is read-only. `pip install --user` fails.
We install to a writable mounted dir and prepend it to `PYTHONPATH`.

### Q11.6 Why the EBS CSI driver?
Pre-1.23 EKS used the in-tree EBS driver; 1.23+ requires the **CSI driver as
an add-on**. We install it and create a `gp3` StorageClass so PVCs land on
modern, faster, cheaper EBS volumes.

### Q11.7 How do you bring the cluster back from snapshots?
`bash infra/cloud/resume_from_snapshots.sh`:
1. `eksctl create cluster brainwatch`.
2. Create EBS volumes **from snapshot IDs in
   `artifacts/eks/snapshots/index.txt`**.
3. Statically provision a PV per restored volume.
4. Re-apply the manifests; PVCs bind to restored PVs by name.
5. Re-upload the wheel + scripts to the code bucket so the init-containers in
   `real-pipeline.yaml` can fetch them.
6. ~15–20 minutes total.

---

## § 12 Testing

### Q12.1 How many tests and what do they cover?
**131 tests, all passing.** Layout:
- `test_anomaly_rules.py`, `test_anomaly_boundaries.py` — v1 + v2 classify, exact thresholds.
- `test_bronze_writer.py` — sha256 dedup + DLQ routing.
- `test_silver_layer.py`, `test_gold_layer.py`, `test_speed_layer.py` — Spark-dependent.
- `test_heedb.py`, `test_icd_codes.py` — real ICD-10 catalogue + HIGH_ACUITY set.
- `test_dashboard_rollups.py` — the canonical rollup builders.
- `test_dead_letter.py`, `test_edf_quality.py`, `test_eeg_inventory.py`,
  `test_subset_manifest.py`, `test_events_contracts.py`, etc.

### Q12.2 Why no `conftest.py`?
`pyproject.toml` already sets `pythonpath=["src"]` and `testpaths=["tests"]`,
so plain `pytest` works. A conftest would just duplicate that.

### Q12.3 How do you avoid requiring Spark for non-Spark tests?
Each Spark test guards itself:
```python
spark_missing = pytest.importorskip("pyspark", reason="...")
```
Or `@pytest.mark.skipif(...)` based on `pyspark` import. If the `spark` extra
isn't installed, those tests **silently skip**; the rest pass.

### Q12.4 What is the testing philosophy?
1. **No external dependencies** — everything uses `tmp_path` and inline data.
2. **Test behaviour, not implementation** — refactor-safe.
3. **Boundary-focused** — exact threshold values are asserted (`0.40`, `0.65`,
   `0.85`, `0.30` quality gate).

### Q12.5 How do you run a single test?
```bash
pytest tests/test_speed_layer.py -v
pytest tests/test_speed_layer.py::test_window_dedup -v
pytest -m "not spark"     # skip Spark-dependent tests
```

### Q12.6 What's the most important test in the suite?
`test_gold_layer.test_broadcast_join_used` — it inspects the Spark plan to
**assert the broadcast hint actually fires** (`F.broadcast` could be silently
ignored if AQE rewrites the plan). This catches the worst-case bug: a silent
shuffle on every gold rebuild.

---

## § 13 Data quality & honesty

### Q13.1 What data quality issues did you find?
1. **Missing duration:** 11,579 rows (3.8%) — flagged, excluded from subset.
2. **Short sessions:** 427 rows < 30 s — likely hookup artifacts.
3. **Schema heterogeneity:** `DurationInSeconds` vs `RecordingDuration`,
   `SiteID` vs `InstituteID` — fallback chain.
4. **Missing patient IDs:** some S0001 rows lack `BDSPPatientID` → we use
   `BidsFolder` as the canonical subject identifier.
5. **Service ambiguity:** 143,369 rows (47%) "UNSPECIFIED" — I0002/I0009 don't
   populate this; default to `["EEG"]`.

### Q13.2 What's synthetic in the project? (be honest)
- **EHR vitals/labs** are templated against the real cohort (real
  `patient_id`s, real `event_time` distribution, but the lab values are
  generated).
- **The anomaly score variance term** is bounded; it's not derived from a
  trained model — the live UDF is rule-based.
- **The full alert "fleet"** in the dashboards is the *output* of running our
  real pipeline on the real EDFs through the rule-based scorer, so it's real
  output but the upstream EHR is partially generated.

### Q13.3 Why the suppression quality gate first?
A high anomaly score from a bad signal is meaningless. Consider:
`anomaly=0.95, quality=0.1`. Without the gate → critical alert → clinician
rushes → disconnected electrode → alert fatigue. Quality gate first → suppress
→ trust preserved. **This is a real clinical design pattern.**

### Q13.4 How do you handle late EHR events?
EHR watermark is **30 minutes** (vs 30 s for EEG). Late EHRs within the
watermark join into the windowed stream; later than that they go straight to
the batch path and are reconciled in the next gold rebuild.

### Q13.5 What goes to the dead-letter queue?
Validation failures in `bronze_writer.write_eeg()`:
- Missing required fields (`patient_id`, `event_time`).
- Malformed timestamps.
- Failed dedup fingerprint compute.
DLQ writes daily JSONL under `data/lake/_dead_letter/<date>.jsonl`.

---

## § 14 Cost, ops, security

### Q14.1 What does it cost to run?
- **Running on EKS:** ~$0.40/hour (control plane $0.10 + 2× m5.xlarge + EBS + NAT).
- **Paused:** ~$1/month (5 EBS snapshots, used-blocks-only billing + 2 S3 buckets).
- **Resume:** ~15–20 minutes from `resume_from_snapshots.sh`.

### Q14.2 Where do most of the costs go when running?
**NAT gateway** is the surprise — ~$32/month plus data-processing fees. We
tear it down when paused. EBS volumes themselves are ~$0.08/GiB-month
provisioned. Control plane is $0.10/hour.

### Q14.3 How do you persist data while compute is off?
EBS **snapshots** — point-in-time copies billed on used blocks, not provisioned
size. Five snapshots for `bronze-pvc`, `checkpoints-pvc`,
`cassandra-data-cassandra-0`, `kafka-data-kafka-0`, `grafana-data`. IDs are
in `artifacts/eks/snapshots/index.txt`.

### Q14.4 Why is the dashboard immune to the cluster being torn down?
Grafana panels read from the **S3 static-website bucket** via the Infinity
datasource. S3 is independent of EKS; the bucket keeps serving the last
exporter output. (Until the next resume, the data is stale — but the
dashboard works.)

### Q14.5 How is the AWS access key handled?
- Stored once in `credentials/rootkey.csv` on this host (gitignored).
- Read into a K8s `Secret` named `aws-credentials` (access-key-id +
  secret-access-key).
- Init-containers and exporter pods mount it as env vars.
- **The current key is exposed** in our git/transcript and **should be
  rotated** — open issue.

### Q14.6 What's the GitHub PAT used for and how is it stored?
Used only by the user to push from this host. Stored in `~/.git-credentials`.
**Should be rotated** along with the AWS key.

---

## § 15 The killer questions (advanced)

### Q15.1 How would you scale to 10× the data?
- **Bronze** is partitioned by `site_id` → per-site Spark jobs (horizontal lever).
- **Kafka** is partitioned by `patient_id` → add partitions, add consumers.
- **EKS** managed node group: autoscale 2 → 8.
- **Cassandra**: add nodes; partition by `patient_id` already gives linear
  write scaling.
- **Bottleneck:** the stream-stream join would re-appear; we'd push to a
  separate state store (e.g., RocksDB state backend in Spark 3.5+).

### Q15.2 How would you go from rule-based scoring to ML?
The MLlib path already exists (`scripts/train_severity_model.py`,
LogisticRegression + VectorAssembler + AUC). Productionisation steps:
1. Persist the model with `model.write().overwrite().save(s3_path)`.
2. Load it in the speed layer via `LogisticRegressionModel.load(...)`.
3. Replace the UDF with `model.transform(df)`.
4. Add a feedback loop: clinician confirmations → training data.

### Q15.3 What happens if Cassandra is down?
Speed layer's `foreachBatch` raises; Spark retries the batch from the
checkpoint. If Cassandra stays down beyond the retry budget, the speed-layer
pod restarts. Once Cassandra returns, the batch replays — no data loss
(Kafka offsets aren't advanced).

### Q15.4 What if Kafka is down?
Producer blocks on `send` (buffer fills). Speed layer's stream stalls (no
new offsets). No data loss as long as the producer's buffer doesn't overflow;
if it does, oldest unsent records are dropped (configurable).

### Q15.5 How do you handle schema evolution on EHR?
EHR uses `payload: dict` (flexible). New fields appear in payload without
migration. The bronze→silver path projects only the fields it knows. The
gold path joins on known keys. **Backwards-compatible adds are free;
breaking changes (rename/remove) get a new `event_type`.**

### Q15.6 What's the SLO for the speed layer?
**Target:** 95th-percentile EEG→alert latency under 60 s.
**Achieved:** ~12 s p95 on EKS (watermark + window + foreachBatch overhead).
SLI = `(now - alert_event_time) < 60` per alert; we don't currently emit this
as a metric to Prometheus (gap; noted).

### Q15.7 What's the data-retention policy?
- Kafka: 24 h retention (configurable).
- Bronze: forever (immutable).
- Silver/Gold: rebuilt nightly from bronze; we keep 30 days of partitions.
- Cassandra alerts: forever (TTL = none); we'd add TTL in production
  (e.g., 90 days).

### Q15.8 What's the disaster-recovery story?
- **Cluster goes away:** restore from EBS snapshots (`resume_from_snapshots.sh`,
  ~15 min).
- **Region goes away:** snapshots are region-scoped → would need cross-region
  copy. Not done (would cost more, out of scope).
- **Code is lost:** project wheel + scripts are versioned in the
  `brainwatch-capstone` S3 bucket; pods fetch from there.
- **Cassandra row is corrupted:** batch layer rebuilds gold + alerts from
  bronze, which is immutable.

### Q15.9 What's a part of the code you'd rewrite if you had a week?
The Cassandra exporter's polling loop. Today it `SELECT *` every 3 s — fine
for the demo, wasteful at scale. Better: subscribe to Cassandra **change
data capture** (CDC) or push from the speed-layer `foreachBatch` directly to
S3 in the same transaction. (Stated as known limitation.)

### Q15.10 What would you measure in production but don't measure now?
- p95/p99 EEG→alert latency.
- Kafka consumer lag per partition.
- Spark micro-batch duration.
- Failed-record rate (DLQ growth).
- Per-patient alert rate (alert fatigue early warning).
- Cassandra read/write latency.
- EBS volume IOPS saturation.
- (We outline the alert thresholds in §14 of the report.)

---

## § 16 Team & roadmap

### Q16.1 Role division?
| Role | Member (gh) | Owns |
|---|---|---|
| Lead / architect | Quang-Hung (`pqhhust`) | speed layer, integration, batch driver |
| Batch-layer owner | Kim-Quan (`quazkim`) | silver, gold, analytics, MLlib |
| Serving owner | Kim-Hung (`hungkimyeu`) | Cassandra sink, anomaly rules v2, EDF producers |
| Kubernetes / deploy | Dat | all K8s manifests, EKS bring-up, resume |
| Demo / tests / EHR | Trang | EHR loader, end-to-end demo, dashboards, tests |

See `CONTRIBUTORS.md` for the canonical module ownership.

### Q16.2 What was each week's deliverable?
- **Week 1:** architecture, profiler (306k rows), manifest (12 sessions),
  4 event contracts, Spark skeleton, anomaly rules, K8s manifests, config
  template, 5 tests.
- **Week 2:** 100h EEG download, Kafka producers, EHR generation, bronze
  writer + DLQ, Streaming consumer, Docker Compose + K8s for Kafka.
- **Weeks 3-4:** batch layer (silver + gold), full speed layer, Cassandra sink.
- **Week 5:** dashboards (5 Grafana), real BDSP cohort, 131 tests.
- **Week 6:** EKS cutover, real-pipeline overlay, pause/resume scripting,
  report.

### Q16.3 What was the hardest decision?
**Dropping the stream-stream EHR join from the live path.** We tried for two
days; Spark's append-mode-only constraint with windowed agg made the watermark
delay too large for a live demo. The decision was to enrich in batch, which
is a real architectural compromise but the right one. Documented as a lesson.

### Q16.4 What's the one thing you wish you'd done earlier?
**Stood up the static-S3-dashboard pattern.** We built the React dashboard
first; the Grafana+Infinity+S3 pattern (which survives cluster teardown)
came later. If we'd done it first, our cost story would have been clean from
week 1.

### Q16.5 What's next if this project continued?
1. Move the rule-based UDF to MLlib LogisticRegression (already trained).
2. Add Prometheus + Grafana for ops metrics (latency, lag, error rates).
3. Add Spark on Kubernetes operator instead of `local[4]` master in the pod.
4. Cross-region snapshot replication for DR.
5. Replace the polling exporter with a `foreachBatch` direct-to-S3 sink.

---

---

## § 17 Hybrid storage (HDFS + S3)

### Q17.1 Why both HDFS and S3? Isn't that two storage layers?
**Different jobs, different layers.** HDFS is the **compute-side** distributed
filesystem (bronze/silver/gold lake + Spark streaming checkpoints — the layer
Spark reads/writes during a pipeline run). S3 is the **serving-side** object
store (rollup JSON for Grafana dashboards). The split lets us:
- Hit the rubric "HDFS or equivalent" literally with **HDFS**.
- Keep the dashboard alive for **~$1/month** when the EKS cluster is torn
  down, because S3 is independent of compute.
- Demonstrate **both** patterns — HDFS for traditional Hadoop-style lake
  workloads, S3 for cloud-native object storage.

### Q17.2 What's the HDFS topology?
**1 NameNode + 2 DataNodes**, deployed as K8s StatefulSets. RF=2 (each block
is on both DataNodes; survives 1 DataNode failure). Block size 64 MiB
(smaller than HDFS's 128 MiB default because our files are small EDF window
records, not multi-GB partitions). UI on port 9870, RPC on 8020.

### Q17.3 Why RF=2 not RF=3?
- **RF=2** is the smallest "distributed" replication that survives one
  DataNode loss — the canonical property of a distributed FS.
- **RF=3** is the production-default but would need 3 DataNodes (+50% storage,
  +50% I/O) for the same demo story.
- The trade-off: with RF=2 + 2 DNs, losing 1 DN means RF temporarily drops to
  1 until rebalance. Acceptable for a demo.

### Q17.4 Which image are you using and why?
`bde2020/hadoop-namenode:2.0.0-hadoop3.2.1-java8` and the matching DataNode
image. They're the **de-facto community standard** for Hadoop on Kubernetes —
configured by environment variables (`CORE_CONF_*`, `HDFS_CONF_*`), auto-format
the NameNode on first boot, used by ~every "HDFS on K8s" tutorial. Alternative
would have been the official `apache/hadoop:3` image with hand-written XML
configs — more boilerplate, same result.

### Q17.5 What goes on HDFS vs S3 vs Cassandra?

| Layer | Storage | Why |
|---|---|---|
| Bronze JSONL (raw) | **HDFS `/lake/bronze`** | Spark reads as input |
| Silver Parquet (clean) | **HDFS `/lake/silver`** | Spark batch output |
| Gold Parquet (rolled-up) | **HDFS `/lake/gold`** | Spark batch output |
| Speed-layer checkpoints | **HDFS `/checkpoints`** | Restart-safety across pod recreates |
| Alerts (live) | **Cassandra `brainwatch.alerts`** | Wide-column NoSQL, fast point lookups |
| Dashboard rollups (JSON) | **S3 static website bucket** | Survives cluster teardown |
| Project code (wheel + scripts) | **S3 `brainwatch-capstone`** | Pods fetch via init-container |

### Q17.6 How does Spark talk to HDFS?
The Spark image (`spark:3.5.5-scala2.12-java17-python3-ubuntu`) ships with
the Hadoop client JARs in `/opt/spark/jars/hadoop-hdfs-*.jar`. When Spark
sees a `hdfs://host:port/path` URI, the Hadoop `FileSystem.get(uri)` returns
a `DistributedFileSystem` impl that does HDFS-protocol RPC. **Zero extra
config** beyond passing the URI.

### Q17.7 What if the NameNode dies?
- **HDFS goes read-only / unavailable** until NameNode comes back. Single
  point of failure in our setup.
- **DataNodes are unaffected** — they still hold the blocks.
- **Recovery:** K8s restarts the NameNode pod; PVC reattaches; NameNode reads
  the FSImage + edits log; back online in ~60 s.
- **Production fix:** HDFS HA with 2 NameNodes + JournalNodes + ZooKeeper.
  Not done for this demo (RF=2 with 1 NN is good enough; documented as a
  known limitation).

### Q17.8 How do you load data into HDFS?
The `hdfs-bronze-loader` Job (in `infra/cloud/k8s-overlays/batch-on-hdfs.yaml`)
mounts the `bronze-pvc` read-only and runs:
```bash
hdfs dfs -put -f /data/lake/bronze/* /lake/bronze/
```
One-shot. Runs after the HDFS overlay is applied. Idempotent — re-runnable
with `-f` (force overwrite).

### Q17.9 Could you have used S3 alone (no HDFS) and still passed the rubric?
**Yes** — the rubric explicitly says "HDFS or equivalent" and S3A is in
Hadoop's source tree as a first-class FileSystem. We did the hybrid for three
reasons:
1. **Literal rubric match** — the NameNode UI is a demo artifact you can
   point at.
2. **Talking point** — "we run a NameNode and 2 DataNodes with RF=2, here's
   `hdfs dfs -du -h /lake`."
3. **Demonstrates we know the trade-off** — kept S3 for the dashboard so we
   keep the cheap-pause property.

### Q17.10 What's the cost story now?

| State | Hourly | Monthly |
|---|---|---|
| Running (everything up) | $0.45/h | (don't leave it running) |
| Paused (cluster down, snapshots kept) | $0.00/h | **~$1.50/mo** (5 EBS snapshots + 2 S3 buckets; HDFS adds 2 more EBS snapshots at most) |
| Dashboard-only (no compute) | $0.00/h | **~$1/mo** (S3 dashboard bucket) |

### Q17.11 What's the demo for HDFS?
```
# Show the NameNode UI
kubectl -n brainwatch port-forward svc/hdfs-namenode 9870:9870
→ open http://localhost:9870
→ Datanodes tab: 2 live DataNodes
→ Utilities → Browse the file system → /lake/silver/eeg

# Show contents in a terminal
kubectl -n brainwatch exec sts/hdfs-namenode -- hdfs dfs -ls -R /lake
kubectl -n brainwatch exec sts/hdfs-namenode -- hdfs dfs -du -h /lake
```

### Q17.12 What's the failure mode you most worry about?
**NameNode is a SPOF.** If we deployed to a real hospital, we'd add HDFS HA
(2 NameNodes + 3 JournalNodes + ZooKeeper for automatic failover). For the
demo, NameNode is on a stable EBS PVC; if the pod dies, K8s restarts it
within 60 s.

### Q17.13 Is the batch one-shot or dynamic?
**Dynamic — two K8s CronJobs fire every 5 minutes.**

```
*/5 * * * *      hdfs-bronze-loader   sync bronze-pvc → HDFS /lake/bronze
2-59/5 * * * *   spark-batch-hdfs     silver + gold rebuild on HDFS  (2-min offset)
```

Settings: `concurrencyPolicy: Forbid` (never overlap),
`startingDeadlineSeconds: 180` (skip if controller stalled),
`successfulJobsHistoryLimit: 2` (bounded etcd footprint),
`ttlSecondsAfterFinished: 1800` (auto-clean completed pods).

This is canonical Lambda — the batch layer is **periodic by definition**.
The speed layer fills the gap between batch runs.

### Q17.14 Why every 5 minutes and not nightly?
- **Demo visibility.** During a live defense the examiner can watch a fresh
  batch fire and `kubectl logs job/spark-batch-hdfs-<latest>` to see real
  output.
- **Cost is trivial** at our scale: each run is ~35-60 s on one t3.xlarge.
  ~$1/day extra at every-5-min cadence.
- **Production would be coarser** — for an 8.5 GiB cohort that doesn't grow,
  nightly is fine. For a live hospital with continuous EDF inflow, 15-min
  or hourly is the realistic cadence.

### Q17.15 Can you trigger a run manually?
Yes — three ways:

```bash
# 1. Create a one-shot Job from the CronJob template
kubectl -n brainwatch create job --from=cronjob/spark-batch-hdfs adhoc-$(date +%s)

# 2. Suspend / resume the cron itself (e.g., during heavy load)
kubectl -n brainwatch patch cronjob spark-batch-hdfs -p '{"spec":{"suspend":true}}'
kubectl -n brainwatch patch cronjob spark-batch-hdfs -p '{"spec":{"suspend":false}}'

# 3. Tweak the schedule live
kubectl -n brainwatch patch cronjob spark-batch-hdfs -p '{"spec":{"schedule":"*/1 * * * *"}}'
```

### Q17.16a How is bronze itself produced — one-shot or continuous?
**Continuous.** The `bronze-streamer` Deployment (one pod, long-running)
reads EDFs one-at-a-time from `s3://brainwatch-capstone/raw_edf/` (17 GiB,
1,571 files), parses each with `mne` to compute measured per-window features
(`signal_quality_score`, `mean_amplitude_uv`, …), writes JSONL into the
`bronze-pvc` mount, sleeps `SLEEP_BETWEEN_EDF=20s`, repeats. State is
persisted to `/data/lake/_state/bronze_streamer.json` so a pod restart
resumes where it left off. The `hdfs-bronze-loader` CronJob then syncs the
PVC into HDFS every 5 min; the `spark-batch-hdfs` CronJob rebuilds silver
+ gold 2 min later. Net effect: bronze/silver/gold counts grow visibly
between successive batch fires.

### Q17.16b How do you visualize cluster state?
The `cluster-state-exporter` pod runs a 30-second loop that queries the K8s
API (via `kubectl` + an in-cluster ServiceAccount), `hdfs dfsadmin -report`
(via `kubectl exec` into the NameNode), and Cassandra (`cqlsh`), then writes
6 flat JSON files to S3: `cluster_summary.json` (scalars for stat panels),
`cluster_pods.json`, `cluster_nodes.json`, `cluster_cronjobs.json`,
`cluster_hdfs.json`, `cluster_hdfs_lake.json`. Plus a derived
`pipeline_metrics.json` that powers the older Pipeline dashboard with live
HDFS-derived numbers (was static before).

### Q17.18 Does bronze store the raw EDF, or just point at it?
**Both, in the simulation: archive pattern.** Each incoming EDF is copied
into `/data/lake/bronze_real/edf/site=<X>/date=<Y>/<file>.edf` AND parsed to
JSONL features. Bronze size therefore grows with each EDF the streamer
pulls — exactly the "real hospital ingest" semantics. We cap the archive at
`ARCHIVE_RAW_CAP_GIB` (default 4 GiB, so HDFS RF=2 stays under 50% of the
40 GiB cluster). Past the cap the streamer continues parsing JSONL but skips
the EDF copy (logged as `archive_CAP_REACHED`).

| | Point pattern | **Archive pattern (what we do)** |
|---|---|---|
| Raw EDF lives in | S3 only | S3 **and** bronze on HDFS |
| Bronze size growth | tiny (~20 MB for 1.5k EDFs) | grows ~12 MB per EDF, up to cap |
| Spark batch | reads JSONL only | reads JSONL only (binary present but unused) |
| Clinician fetches raw | follow `source_uri` to S3 | inside bronze OR S3 |
| HDFS footprint (×RF=2) | minimal | larger; capped for safety |

**Reasoning for archive:** this is a **hospital simulation**. In production
neuro-ICU systems, raw EEG is archived alongside derived analytics so:
- Clinicians can replay the signal for re-interpretation
- Regulators can audit the source of every alert
- ML re-training can use the raw signal directly

For pure analytics (no clinical re-review), the point pattern (S3 only)
would suffice and save HDFS space. We picked archive for fidelity to the
real-world workflow.

**What bronze size on the dashboard measures:** real bytes — the JSONL
features **plus** the archived EDF binaries — growing in real-time as the
streamer trickles EDFs from S3.

### Q17.19 What's the difference between "Raw EDF on S3" and "Bronze size"?
Two stats on the Pipeline dashboard:
- **Raw EDF on S3 (GiB):** `17.05` — the **upstream** raw archive on
  S3. Source of truth for `bronze-streamer`; doesn't change unless we ingest
  more.
- **Bronze size (GiB):** grows from `0.02` upward as the streamer processes
  EDFs and archives them into `bronze_real/edf/`. Eventually plateaus at the
  archive cap.

Both reflect "real data on the cluster." The S3 number is what's
*available to be ingested*; the bronze number is what's *been ingested
into the lake*.

### Q17.17 How would you upgrade from CronJob to streaming bronze→silver→gold?
Spark Structured Streaming + Delta Lake. Replace `run_batch.py` with:

```python
spark.readStream.format("delta").load("hdfs://.../lake/bronze") \
     .dropDuplicates(["patient_id","session_id","event_time"]) \
     .withColumn("quality_flag", quality_udf(...)) \
     .writeStream.format("delta") \
     .outputMode("append") \
     .trigger(availableNow=True)        # process new data, exit; called by CronJob
     .option("checkpointLocation", "hdfs://.../checkpoints/silver") \
     .start("hdfs://.../lake/silver")
```

Plus a `MERGE INTO` for incremental gold rollups. Adds Delta Lake as a
dependency. ~1 week of work, +$500/mo at our scale. See
[`AUTO-TRIGGER-MECHANISMS.md` §8](AUTO-TRIGGER-MECHANISMS.md) for the full
upgrade path.

---

*See also: `STUDY-GUIDE.md` (the reading order across all docs),
`CHEATSHEET.md` (single-page printable), `PRESENTATION-GUIDE.md` (longer pitch
form), `final-report.md` (the formal write-up).*

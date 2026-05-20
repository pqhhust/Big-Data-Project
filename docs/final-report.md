# BrainWatch — Final Report

**Course:** IT4043E — Big Data Storage and Processing (HUST SOICT, Spring 2026)
**Instructor:** Viet-Trung Tran, PhD
**Team:** Quang-Hung (lead/architect), Kim-Hung, Kim-Quan, Dat, Trang
**Repository:** `github.com/pqhhust/Big-Data-Project`
**Submission tag:** `v1.0.0`

---

## 1. Problem Definition

### 1.1 Selected Problem
Hospital-scale **near-real-time electroencephalography (EEG) anomaly detection with electronic health-record (EHR) context enrichment**. The system continuously ingests multi-site EEG recordings together with asynchronous EHR updates (vital signs, lab results, medications, notes) and raises severity-classified alerts when EEG signal-quality features combine with high-risk clinical context (e.g., critical-lab spike during a low-quality EEG window).

### 1.2 Suitability for Big Data
The workload satisfies all five Vs:

| V | BrainWatch workload |
|---|---|
| **Volume** | A single 19-channel 200 Hz EEG study produces ~70 MB / hour; a 50-bed neuro-ICU produces ~4 TB / month of raw signals. Our reproducible demo run ingests ≥8 GiB of bronze events in a single sprint. |
| **Velocity** | EEG arrives as continuous 10 s windowed chunks (≈360 events / patient / hour); EHR arrives asynchronously with bursts during rounds. The pipeline must produce alerts within minutes, not hours. |
| **Variety** | EEG is numerical time-series (EDF); EHR is semi-structured JSON (vital signs, labs, free-text notes, medication orders) sourced from heterogeneous systems (Epic, Cerner). |
| **Veracity** | Multi-site clinical data carries duplicates, late-arriving updates, version conflicts (encounter `v1 → v2 → v3`), and out-of-range sampling rates that must be cleansed before downstream use. |
| **Value** | Each clinically-actionable alert prevents hours of nurse review time; missed critical-lab + low-quality-EEG correlations directly impact patient safety. |

### 1.3 Scope and Limitations
- **In scope.** End-to-end Lambda pipeline (ingest → bronze → silver → gold + streaming alerts), Kubernetes deployment, reproducible synthetic-at-scale demo, unit-and-integration test coverage, a Cassandra serving layer.
- **Out of scope.** Federated learning across hospital sites; deep-learning anomaly scoring (we deliberately keep the speed-layer scoring interpretable for the capstone); GPU EEG decoding; clinician-facing UI beyond a Matplotlib demo dashboard.
- **Limitations.** Demo data uses synthetic EEG-chunk events derived from BDSP metadata rather than the full raw EDF binary stream (the local lab subset constrains the raw download); single-node Cassandra (replication factor 1); local-mode Spark for batch (no separate executor pods).

---

## 2. Architecture and Design

### 2.1 Lambda vs. Kappa — Decision

We chose **Lambda Architecture**. The decision rests on three constraints from the problem:

| Requirement | Lambda | Kappa |
|---|---|---|
| Historical EEG/EHR backfill (research cohorts, retraining) | Native (batch + speed) | Requires stream replay through the entire query path |
| Independent debugging of slow- vs. fast-path | Two pipelines, two failure domains | Single pipeline — a bug stops everything |
| Recompute the gold layer with a new aggregation | Re-run the daily CronJob | Replay the full stream from Kafka |
| Team parallelism (5 engineers, 1 sprint) | Layer-based ownership | Single-stream serial work |

Lin (2017, *IEEE Internet Computing*) argues Kappa is preferable when state remains bounded and replay is cheap; for our clinical setting with multi-month research backfills the replay cost is prohibitive.

### 2.2 Components and Roles

```
                ┌────────────────────────────────────────────────────────┐
                │                   INGESTION LAYER                      │
                │   ┌──────────────┐    ┌────────────────────────────┐   │
   EEG (BDSP) ─▶│   │ EEG producer ├───▶│   Kafka topic eeg.raw      │   │
                │   └──────────────┘    └────────────────────────────┘   │
   EHR (synth) ▶│   ┌──────────────┐    ┌────────────────────────────┐   │
                │   │ EHR producer ├───▶│  Kafka topic ehr.updates   │   │
                │   └──────────────┘    └────────────────────────────┘   │
                │                       │ Dead-letter queue (JSONL)  │   │
                └───────────────────────┼────────────────────────────┼───┘
                                        ▼                            ▼
                ┌───────────────────────────────────────────────────────┐
                │                BRONZE — Spark Structured Streaming    │
                │     Kafka → Parquet/JSONL, SHA-256 dedup,             │
                │     partition by site_id/date, DLQ on validation fail │
                └───────────────────────────────────────────────────────┘
                            │                              │
                            │ (slow path)                  │ (fast path)
                            ▼                              ▼
                ┌───────────────────────┐      ┌──────────────────────────┐
                │  BATCH — CronJob      │      │  SPEED — Spark Streaming │
                │   Silver: dedup +     │      │  EEG ⋈ EHR ±30min,       │
                │   latest-version-per- │      │  windowed agg, UDF score │
                │   encounter           │      │  classify_v2 severity    │
                │   Gold: broadcast-    │      │                          │
                │   join patient_dim,   │      │  ── foreachBatch sink ── │
                │   daily rollups       │      │                          │
                └──────────┬────────────┘      └─────┬──────────────┬─────┘
                           │                         │              │
                           │                  ┌──────▼──────┐  ┌────▼────┐
                           ▼                  │  Cassandra  │  │  Kafka  │
                ┌─────────────────────┐       │   alerts +  │  │ alerts. │
                │ Gold zone Parquet   │       │ patient_st. │  │ anomaly │
                │ patient_features,   │       └─────────────┘  └─────────┘
                │ alert_summary       │
                └──────────┬──────────┘
                           ▼
                ┌─────────────────────┐
                │ Matplotlib dash     │
                │ (severity hist,     │
                │ alert timeline,     │
                │ top-N patients)     │
                └─────────────────────┘
```

### 2.3 Data Flow & Interaction Diagrams

| Stage | Input | Output | Latency budget |
|---|---|---|---|
| Ingest → Kafka | EEG/EHR producers | `eeg.raw`, `ehr.updates` | < 100 ms / event |
| Kafka → Bronze | Spark streaming `bronze_ingest` | Parquet/JSONL partitioned `site=*/date=*` | Watermark 10 min (EEG) / 30 min (EHR) |
| Bronze → Speed | Spark streaming `speed_layer` | Alerts to Cassandra + `alerts.anomaly` | 30 s `processingTime` trigger |
| Bronze → Silver → Gold | Daily CronJob `run_batch.py` | `silver/{eeg,ehr,_dim/patient}`, `gold/patient_features`, `gold/alert_summary` | Off-peak nightly window |
| Serving | Cassandra alerts + Matplotlib dashboard | PNGs + CQL queries | Sub-second per query |

---

## 3. Implementation Details

### 3.1 Codebase Layout

```
Big-Data-Project/
├── src/brainwatch/
│   ├── contracts/           # Immutable event dataclasses (EEGChunkEvent, EHREvent, AlertEvent)
│   ├── ingestion/           # Producers, BronzeWriter (SHA-256 dedup), DLQ, kafka_helpers (FileProducer fallback)
│   ├── processing/          # bronze_ingest, silver_layer, gold_layer, speed_layer
│   ├── serving/             # cassandra_sink, anomaly_rules (compute_anomaly_score, classify_v2), alert_publisher
│   └── config/              # YAML settings loader
├── scripts/                 # CLI orchestration (replay_to_kafka, end_to_end_demo, generate_demo_data_at_scale, run_batch)
├── tests/                   # 67 pytest tests — 59 always-on + 8 Spark-guarded
├── infra/
│   ├── docker/              # docker-compose (Kafka KRaft + Spark master/worker)
│   └── k8s/                 # 9 manifests + deploy.sh / teardown.sh, namespace = `brainwatch`
└── dashboard/               # React+Vite scaffold for the live dashboard
```

### 3.2 Spark Features Demonstrated

The rubric requires multiple advanced Spark patterns. Mapping each to a concrete file:

| Rubric category | Where in the code | Why |
|---|---|---|
| **Window functions** | `silver_layer.build_ehr_silver` — `row_number() over partitionBy(patient_id, encounter_id) orderBy version DESC` | Keep only the latest EHR version per encounter |
| **Complex aggregations / pivot-style indicators** | `gold_layer.build_patient_features` — `max(when(...).otherwise(0))` and `sum(when(...).otherwise(0))` on event-type | One row per patient/day with multiple severity-relevant flags |
| **Custom UDFs** | `speed_layer.build_streaming_pipeline` — `F.udf(compute_anomaly_score)` for the 0–1 anomaly score | Business-logic blend (chunk count + signal quality + critical-lab + medication changes) |
| **Broadcast join (unbalanced)** | `gold_layer.build_patient_features` — `eeg.join(F.broadcast(patient_dim), …)` | The patient-dim table is small (~10⁴ rows); broadcast avoids a shuffle that would otherwise dominate runtime |
| **Sort-merge join (large)** | `gold_layer.build_patient_features` — EEG ⋈ EHR on `patient_id` + ±30 min predicate | Both inputs are large; Catalyst picks SMJ when neither side fits the broadcast threshold |
| **Partition pruning / coalesce** | `silver_layer` writes partition by `site_id, ingestion_date` and `coalesce(4)` to target 64–256 MB files | Downstream queries on a single site read 1/N partitions only |
| **Caching / persistence** | Spark batch driver `spark.sql.autoBroadcastJoinThreshold=50 MiB` | Forces broadcast threshold; we benchmarked the join with `df.explain()` (§ 7) |
| **Structured Streaming** | `bronze_ingest` (Kafka source) and `speed_layer` (Parquet streaming source) | Two output modes used: append (bronze writer) and update (speed-layer foreachBatch) |
| **Watermarking / late-data handling** | `eeg_df.withWatermark("event_time", "10 minutes")`, `ehr_df.withWatermark("event_time", "30 minutes")` | EEG is sub-second jitter, EHR can arrive minutes late |
| **State management / exactly-once** | Spark structured streaming + checkpoint dir on the `checkpoints-pvc` Kubernetes PVC | Spark commits offsets atomically with state snapshots; Kafka offsets advance only on successful batch |
| **Statistical aggregation** | `gold_layer.build_patient_features` — `avg(sampling_rate_hz)` and counts | Daily descriptive stats per patient |

### 3.3 Configuration

- **Runtime config:** `configs/project.example.yaml` (Kafka topics, lake paths, watermarks, target hours). Loaded by `brainwatch.config.settings`.
- **Environment variables:** `BDSP_CREDENTIALS` (S3 rootkey path), `KAFKA_BOOTSTRAP` (broker), `CASSANDRA_HOST` (contact point).
- **Kubernetes ConfigMap:** `brainwatch-config` injected into Spark pods via `envFrom: configMapRef`.

### 3.4 Deployment Strategy

```bash
bash infra/k8s/deploy.sh                # full apply, namespace brainwatch
NAMESPACE=foo bash infra/k8s/deploy.sh  # alt namespace
bash infra/k8s/deploy.sh --dry-run      # client-side dry run

bash infra/k8s/teardown.sh              # keep PVCs
bash infra/k8s/teardown.sh --delete-pvcs  # destroy data (double-prompts)
```

Deploy order: `namespace → configmap → PVCs → zookeeper → kafka → cassandra → spark-streaming → spark-batch CronJob`, with `kubectl rollout status … --timeout=300s` after each layer.

**Resource budget (per pod):**

| Workload | CPU request / limit | Memory request / limit | Storage |
|---|---|---|---|
| Kafka StatefulSet | 0.5 / 1 | 512 Mi / 1 Gi | 20 Gi PVC |
| Cassandra StatefulSet | 0.5 / 2 | 1 Gi / 4 Gi | 20 Gi PVC (`cassandra-pvc`) |
| Spark streaming Deployment | 1 / 2 | 2 Gi / 4 Gi | 20 Gi bronze (RO) + 5 Gi checkpoints |
| Spark batch CronJob | 1 / 2 | 2 Gi / 4 Gi | bronze (RO) + silver + gold + checkpoints |

### 3.5 Monitoring

- **Spark UI** exposed on port 4040 via the `spark-streaming-ui` ClusterIP Service (`kubectl port-forward svc/spark-streaming-ui 4040:4040`).
- **Streaming-query progress** via Spark's built-in `lastProgress` / `recentProgress` — captured by the dashboard script.
- **Cassandra health** via `nodetool status` readiness probe.
- **Kafka topic lag** via `kafka-consumer-groups.sh`.

---

## 4. Batch Layer

### 4.1 Bronze → Silver

`silver_layer.py` reads partitioned bronze and produces three Silver datasets:

```
silver/
├── eeg/        partitioned by site_id, ingestion_date
├── ehr/        partitioned by ingestion_date
└── _dim/patient/   (broadcast-sized patient dimension)
```

| Transformation | Spark expression | Rationale |
|---|---|---|
| Dedup EEG | `dropDuplicates(["patient_id","session_id","event_time"])` | Bronze has its own SHA-256 dedup but silver re-asserts after at-least-once Kafka semantics |
| Filter bad rows | `where 0 < sampling_rate_hz <= 1000` | Defensive — drops corrupted records |
| Quality flag | `when sampling_rate_hz < 100 → LOW_SR; window_seconds < 5 → SHORT_WINDOW; else OK` | Downstream pipelines can filter |
| Latest-version EHR | `row_number().over(W.partitionBy("patient_id","encounter_id").orderBy(col("version").desc())) == 1` | Resolves the v1→v2→v3 encounter mutations |
| Patient dim | `union(eeg.patient_id, ehr.patient_id).distinct() + sha1(patient_id)[:12] as patient_key` | Stable key, small enough to broadcast in §5 |

### 4.2 Silver → Gold

`gold_layer.build_patient_features` does the headline aggregation:

1. **Broadcast join** EEG with patient_dim — `df.explain()` confirms `BroadcastHashJoin`.
2. **Range-predicate join** EEG with EHR on `patient_id` and a `INTERVAL 30 MINUTES` window around `event_time`.
3. **groupBy** `(patient_id, to_date(event_time) as event_date)` and emit:
   - `n_eeg_chunks` — `count(session_id)`
   - `mean_sampling_rate` — `avg(sampling_rate_hz)`
   - `has_critical_lab_today` — `max(when(event_type == 'critical_lab', 1).otherwise(0))`
   - `n_medication_changes` — `sum(when(event_type == 'medication', 1).otherwise(0))`
4. **Write** `gold/patient_features/` partitioned by `event_date`.

A second job `build_alert_summary` consumes the Cassandra alerts JSONL export and writes daily counts by severity.

---

## 5. Speed Layer

### 5.1 Stream-Stream Join

`speed_layer.build_streaming_pipeline` is the headline streaming query:

- **Sources.** Two streaming Parquet readers over `bronze/eeg` and `bronze/ehr` (bronze is already authoritative — re-reading Kafka here would duplicate JSON parsing).
- **Watermarks.** 10 min (EEG), 30 min (EHR) — chosen empirically; EHR can lag during rounds.
- **Join.** Left-outer on `patient_id`, post-filter `|eeg.event_time − ehr.event_time| ≤ 30 min`. Spark adds the watermark constraint to the join state-store so memory is bounded.
- **Windowed aggregation.** `F.window(event_time, "1 minute", "30 seconds")` with: count, mean sampling rate, max critical-lab indicator, max channel count.
- **Anomaly UDF.** `compute_anomaly_score()` (see § 6.2).
- **Sink.** `foreachBatch(publish_alerts)` writes to Cassandra (durable) and the `alerts.anomaly` Kafka topic (fan-out).
- **Trigger.** `processingTime = 30 s` — operator-visible latency, configurable.

### 5.2 Late-Data Handling

Records arriving after the watermark are dropped silently by Structured Streaming. The DLQ catches *malformed* events upstream at the bronze writer level. We do not currently re-route late-arriving valid events — they would be picked up by the next batch-layer rebuild of the gold zone, which is the entire point of the Lambda design.

---

## 6. Serving

### 6.1 Cassandra Schema

```sql
CREATE KEYSPACE brainwatch WITH replication = {'class':'SimpleStrategy','replication_factor':1};

CREATE TABLE brainwatch.alerts (
  patient_id   text,
  alert_time   timestamp,
  severity     text,
  anomaly_score float,
  explanation  text,
  PRIMARY KEY (patient_id, alert_time)
) WITH CLUSTERING ORDER BY (alert_time DESC);

CREATE TABLE brainwatch.patient_state (
  patient_id          text PRIMARY KEY,
  last_alert_time     timestamp,
  last_severity       text,
  last_anomaly_score  float
);
```

Read pattern: `SELECT * FROM brainwatch.alerts WHERE patient_id = ? LIMIT ?` — bounded scan on a single partition, clustering-order DESC means the most recent alert is row 1.

### 6.2 Anomaly Rules v2

```python
def compute_anomaly_score(features: dict) -> float:
    chunk_term     = min(features.get("eeg_chunk_count", 0) / 60.0, 1.0)
    quality_term   = 1.0 - features.get("signal_quality_score", 1.0)
    critical_term  = 0.6 if features.get("has_critical_lab") else 0.0
    meds_term      = min(features.get("n_medication_changes_24h", 0) / 5.0, 1.0)
    return max(0.0, min(0.30*chunk_term + 0.25*quality_term +
                        0.30*critical_term + 0.15*meds_term, 1.0))
```

| Score / context | Severity |
|---|---|
| `signal_quality < 0.3` | `suppressed` (insufficient evidence) |
| `≥ 0.85` OR (`≥ 0.60` AND `has_critical_lab`) | `critical` |
| `0.65 – 0.85` | `warning` |
| `0.40 – 0.65` | `advisory` |
| `< 0.40` | `normal` |

The thresholds are load-bearing — `tests/test_anomaly_rules.py` and `tests/test_serving.py` lock them down (10 + 4 tests).

### 6.3 Alert Publisher

Dual-sink via `writeStream.foreachBatch(publish_alerts)`:
- **Cassandra** — `BatchStatement` of `INSERT INTO alerts`; idempotent because `(patient_id, alert_time)` is unique.
- **Kafka** — `alerts.anomaly` topic, key = `patient_id` for partition-affinity.

Severity filter: only `critical / warning / advisory` are published — `normal` and `suppressed` stay in the gold zone only.

---

## 7. Performance

### 7.1 Bronze-Generation Throughput (Local)

| Run | Events written | Bronze size | Elapsed | Throughput |
|---|---|---|---|---|
| Smoke | 295 K | 66 MiB | 6.6 s | 44 k events/s |
| **Full demo** | **37,250,000** (21.1 M EEG + 16.1 M EHR) | **8.20 GiB** (4.5 GiB EEG + 3.8 GiB EHR) | **789.5 s** | **47.2 k events/s** |

Both runs used a single-process `BronzeWriter` (no parallelism). The dedup
``_seen`` set grew to ~37 M entries, consuming ~1.7 GiB resident; for a
production rewrite the dedup set should be externalised (e.g., RocksDB) or
the writer parallelised.

### 7.2 Broadcast vs. Sort-Merge

`df.explain()` snippet from `gold_layer.build_patient_features` (eeg ⋈ patient_dim):

```
== Physical Plan ==
*(2) BroadcastHashJoin [patient_id#12], [patient_id#34], LeftOuter, BuildRight
:- *(2) FileScan parquet silver/eeg ...
+- BroadcastExchange HashedRelationBroadcastMode ...
   +- *(1) FileScan parquet silver/_dim/patient ...
```

Setting `spark.sql.autoBroadcastJoinThreshold = 50 MiB` is sufficient because the patient_dim table is ~120 KiB. If we let Catalyst fall back to SortMergeJoin, the same query takes ~3.4× longer on our test fixtures.

### 7.3 Partition Pruning

After silver writes `partitionBy(site_id, ingestion_date)`, a query restricted to one site reads ~1/N parquet files:

```python
spark.read.parquet("silver/eeg") \
     .filter(F.col("site_id") == "I0003") \
     .filter(F.col("ingestion_date") == F.lit("2026-05-19")) \
     .explain()
# PartitionFilters: [isnotnull(site_id), isnotnull(ingestion_date),
#  (site_id = I0003), (ingestion_date = 2026-05-19)]
# PushedFilters: []
```

### 7.4 Coalesce

`coalesce(4)` after silver/gold writes keeps file sizes in the 64–256 MiB sweet spot for HDFS-style block alignment. Without it we get hundreds of tiny files and downstream reads pay metadata overhead.

---

## 8. Deployment

### 8.1 Topology

```
                                ┌────── namespace brainwatch ─────┐
  kubectl port-forward          │                                  │
   svc/spark-streaming-ui ─────▶│  Deployment  spark-streaming     │
                                │                                  │
                                │  StatefulSet kafka  (1 replica)  │
                                │  Deployment  zookeeper (1)       │
                                │  StatefulSet cassandra (1, PVC)  │
                                │  CronJob     spark-batch (03 UTC)│
                                │  ConfigMap   brainwatch-config   │
                                │  5×PVC: bronze, silver, gold,    │
                                │   checkpoints, cassandra         │
                                └──────────────────────────────────┘
```

### 8.2 Runbook (deploy.sh order)

1. `namespace.yaml` — creates the `brainwatch` namespace.
2. `configmap.yaml` — injects topic names, paths, hyper-params.
3. `persistent-volumes.yaml` — 5 PVCs (bronze 20 Gi, silver 20 Gi, gold 10 Gi, checkpoints 5 Gi, cassandra 20 Gi).
4. `zookeeper-deployment.yaml` → wait for `Available`.
5. `kafka-statefulset.yaml` → wait for `Ready`.
6. `cassandra-statefulset.yaml` → wait for `Ready` (60 s nodetool warm-up).
7. `spark-streaming-deployment.yaml` → wait for `Available`.
8. `spark-batch-cronjob.yaml` — registers daily 03:00 UTC schedule.

Validated end-to-end with `kubeconform` (offline, schema-aware): **17 resources, all valid** (`kubeconform -summary -verbose -ignore-missing-schemas infra/k8s/*.yaml`).

### 8.3 Common Failure Modes

| Symptom | Root cause | Fix |
|---|---|---|
| `spark-streaming` pod `CrashLoopBackoff` | Kafka not ready when Spark starts | Add `initContainer` waiting on `kafka:9092`; or simply re-deploy after `kafka-0` Ready |
| `cassandra-0` Pending | Default `StorageClass` missing | `kubectl get storageclass` and adjust the PVCs (set `storageClassName`) |
| Spark UI 4040 unreachable | Service selector mismatch | `kubectl describe svc spark-streaming-ui` — confirm `app=spark-streaming` selector |

---

## 9. Testing

### 9.1 Strategy

| Layer | Test type | Tools | Count |
|---|---|---|---|
| Contracts / pure functions | Unit | pytest | 10 anomaly + 5 EHR + 4 download + 3 kafka_helpers + 3 DLQ + 3 EEG-producer + 5 EHR-normalizer + … |
| Bronze writer | Unit + tmp_path filesystem | pytest | 4 |
| Silver / gold | Integration with local `SparkSession(local[2])` | pytest + pyspark | 6 (silver 3 + gold 3) |
| Speed layer | Structural (function signature) | pytest | 1 — full streaming is integration territory |
| Serving | Unit + `_FakeSession` mocks | pytest | 9 |
| **Total** | | | **67 tests, 67 passing** |

### 9.2 Fixtures

Real Parquet round-trips for silver/gold use `tmp_path` and a session-scoped `SparkSession` fixture pinned to `master('local[2]')` with `spark.ui.enabled=false`. Cassandra and Kafka are mocked through deterministic `_FakeSession` / `FileProducer` stubs already scaffolded in `kafka_helpers.py`.

### 9.3 Coverage Gaps

- **End-to-end Kubernetes integration test.** Requires an actual cluster — out of scope for the unit suite; covered manually by `bash infra/k8s/deploy.sh --dry-run` + `kubeconform` validation.
- **Late-data semantic test.** Watermark behavior is exercised by the speed layer in production but not unit-tested.
- **Cassandra schema migration test.** Would need a Cassandra container in CI; currently we ship the schema in `init_keyspace()` and let it CREATE IF NOT EXISTS.

---

## 10. Results

### 10.1 Pipeline run (synthetic-at-scale demo, measured 2026-05-20)

| Metric | Value |
|---|---|
| Bronze zone | **8.20 GiB** (4.5 GiB EEG + 3.8 GiB EHR, JSONL, site/date partitions) |
| EEG event count | **21,118,196** |
| EHR event count | **16,131,804** |
| Generator throughput | **47.2 k events / second** (single-process writer, 789.5 s wall-clock) |
| Silver zone (Parquet + Snappy) | **198 MiB** — **~42× compression** vs bronze JSONL |
| Gold zone (Parquet + Snappy) | **9.3 MiB** |
| `gold/patient_features` rows | **1,195,332** (one row per patient × day) |
| **Full batch driver (bronze → silver → gold)** | **47.8 s on 8.2 GiB** — local-mode 16-core, 24 GiB heap, 256 shuffle partitions |
| Cassandra round-trip (`init_keyspace + write_alerts + query_recent_alerts`) | sub-second |

Full numbers and the `df.explain()` plans are captured in
`artifacts/demo/generate_run.log` and `artifacts/demo/batch_run.log`.

### 10.2 Demo Dashboard

`scripts/demo_dashboard.py` emits four artifacts to `artifacts/demo/figures/`:

- `severity_histogram.png` — count by severity tier
- `alert_timeline.png` — alert rate vs. wall-clock
- `anomaly_score_distribution.png` — score histogram with severity bands
- `top_patients.md` — top-5 patients table

A 3-minute screen-capture of the live pipeline is in `artifacts/demo/demo.mp4` (added in the report week).

---

## 11. Lessons Learned

The course rubric requires lessons across 11 categories using the **Problem / Approaches / Solution / Key Takeaways** template. We frame each lesson from a concrete moment in the sprint.

### Lesson 1: Data Ingestion — Heterogeneous sources, dedup, late-arriving updates

#### Problem Description
- **Context.** EEG arrives from BDSP via S3 (binary EDF, multi-GB per study) while EHR arrives as small JSON events from clinical systems (Epic, Cerner) with version mutations (`v1 → v2 → v3`).
- **Challenges.** S3 fetch latency, duplicate events from at-least-once Kafka producers, EHR encounters that get updated minutes after the first version.
- **Impact.** Without explicit dedup and version resolution the silver zone double-counted and produced false anomaly alerts.

#### Approaches Tried
- **Approach 1 — naïve "store everything" in bronze.** Simplest but pushed dedup work onto every downstream query.
- **Approach 2 — bronze-only dedup via SHA-256 fingerprint** of `(patient_id, session_id, event_time)`. Catches Kafka redeliveries but not EHR version mutations.
- **Approach 3 — bronze SHA-256 dedup + silver `row_number()` latest-version-per-encounter.** Two cheap checks at two layers; bronze stays fast, silver is authoritative.

#### Final Solution
`BronzeWriter._event_fingerprint` (16-hex SHA-256 of `patient_id|session_id|event_time`) + `silver_layer.build_ehr_silver` window function `row_number().over(W.partitionBy(...).orderBy(version.desc()))`. Validation failures are routed to `DeadLetterQueue` (JSONL files per day under `data/lake/_dead_letter/`). 4 unit tests in `test_bronze_writer.py` lock the dedup behavior down.

#### Key Takeaways
- Dedup at write time (bronze) AND at the model boundary (silver) — the two protect against different failure modes (redelivery vs. mutation).
- A DLQ that's just append-only JSONL is enough for a capstone; resist the urge to add a Kafka DLQ topic until you have a consumer for it.
- Encode the encounter version explicitly in the contract (`version: int`); without it `row_number()` has nothing to order by.

---

### Lesson 2: Data Processing with Spark — Job optimization, memory, partition tuning

#### Problem Description
- **Context.** The gold-layer patient-features rollup joins three datasets: EEG (large), EHR (medium), patient_dim (tiny).
- **Challenges.** Without intervention Catalyst chose SortMergeJoin for `eeg ⋈ patient_dim`, paying a full shuffle for what should be a broadcast.
- **Impact.** A 1.4 GiB shuffle on the test fixtures, ~3.4× slower wall-clock than the broadcast plan.

#### Approaches Tried
- **Approach 1 — leave it to Catalyst.** Catalyst's cost-based optimizer can pick broadcast when stats are present, but our parquet files lacked freshly-collected stats, so it fell back to SMJ.
- **Approach 2 — `ANALYZE TABLE … COMPUTE STATISTICS`.** Worked but added a maintenance step.
- **Approach 3 — explicit `F.broadcast(patient_dim)` hint plus `spark.sql.autoBroadcastJoinThreshold=50 MiB`.** Idiomatic and self-documenting.

#### Final Solution
`gold_layer.build_patient_features` uses `F.broadcast(patient_dim)`; `run_batch.py` sets `spark.sql.autoBroadcastJoinThreshold=50 * 1024 * 1024`. `tests/test_gold_layer.py::test_patient_dim_join_uses_broadcast` parses `queryExecution().toString()` and asserts `BroadcastHashJoin` appears in the plan — preventing accidental regressions to SMJ.

#### Key Takeaways
- Always inspect `df.explain()` for the join strategy on small-side tables.
- Encoding the optimization as an assertion in a test is the only way to prevent it from rotting on the next refactor.
- `coalesce(4)` after silver/gold writes — small files kill downstream reads; 64–256 MiB target.

---

### Lesson 3: Stream Processing — Exactly-once, windowing, state, recovery

#### Problem Description
- **Context.** The speed layer must produce alerts within seconds of an EEG anomaly *and* survive pod restarts without duplicate alerts.
- **Challenges.** Spark Structured Streaming guarantees exactly-once only if (a) the source is replayable, (b) the sink is idempotent, (c) state lives in a checkpoint directory that survives restart.
- **Impact.** Early drafts wrote to Cassandra without a stable primary key — restart produced duplicate alerts and confused operators.

#### Approaches Tried
- **Approach 1 — write each batch as INSERT with auto-generated UUID.** Idempotent only per-batch; restart re-emitted everything.
- **Approach 2 — write with `(patient_id, alert_time)` as the Cassandra primary key + `IF NOT EXISTS`.** Idempotent but Cassandra LWT is expensive.
- **Approach 3 — `(patient_id, alert_time)` primary key + plain UPSERT.** Cassandra `INSERT` is implicitly an upsert on the PK; re-emission overwrites the same row, which is fine because the row content is deterministic.

#### Final Solution
Cassandra `alerts` table is `PRIMARY KEY (patient_id, alert_time) WITH CLUSTERING ORDER BY (alert_time DESC)`. The speed layer writes via `foreachBatch` whose sink is `publish_alerts(batch_df, batch_id, …)`; checkpoint directory is the `checkpoints-pvc` Kubernetes PVC so restarts pick up exactly where we left off.

#### Key Takeaways
- Exactly-once is a three-leg stool: replayable source + idempotent sink + checkpointed state. Drop any leg and you collapse.
- Watermarks bound the join state-store: without them stream-stream joins eat memory linearly with time.
- Test the sink for idempotency separately from the streaming query (`test_serving.test_write_alerts_then_query_recent_alerts_roundtrip`).

---

### Lesson 4: Data Storage — Formats, partitioning, compression, hot/cold

#### Problem Description
- **Context.** Bronze stores raw JSONL for human-debuggability; silver and gold use Parquet for efficient batch reads. Partition choice impacts both write throughput and query selectivity.
- **Challenges.** Picking partition columns that match the *query* pattern, not the *ingestion* pattern.
- **Impact.** Early choice of `partition by event_date` only led to single-massive-partition imbalance for high-volume sites.

#### Approaches Tried
- **Approach 1 — `partition by event_date` only.** One partition per day; one site (`I0003`) dominated and produced one 4-GiB Parquet file.
- **Approach 2 — `partition by site_id` only.** Better balance but day-scoped queries scan the whole dataset.
- **Approach 3 — `partition by site_id, ingestion_date`.** Multi-level pruning; both query axes selective.

#### Final Solution
`silver/eeg/site=<id>/ingestion_date=<YYYY-MM-DD>/…parquet`. EHR uses only `partition by ingestion_date` (no per-site dimension). Snappy compression by default (Spark's Parquet default) — ~5× smaller than the bronze JSONL on our test data.

#### Key Takeaways
- Choose partition columns from the read pattern, not the write pattern.
- For Lambda, bronze stays JSONL (debuggable), silver+gold become Parquet (queryable).
- `coalesce` after the write — small Parquet files are queryable but the metadata read dominates.

---

### Lesson 5: System Integration — Service discovery, error handling, fault isolation

#### Problem Description
- **Context.** Spark pods need to reach Kafka and Cassandra; both run in the same Kubernetes namespace but their addresses come from different sources (StatefulSet pod-DNS vs. headless Service).
- **Challenges.** A Cassandra contact point of just `cassandra-svc` doesn't resolve to a pod; you need `cassandra-0.cassandra-svc.brainwatch.svc.cluster.local`.
- **Impact.** Spark crashed at startup with `NoHostAvailableException` until the FQDN was wired in.

#### Approaches Tried
- **Approach 1 — environment variable `CASSANDRA_HOST=cassandra-svc`.** Service exists but is headless; clients couldn't get a connectable IP.
- **Approach 2 — `CASSANDRA_HOST=cassandra-0.cassandra-svc.brainwatch.svc.cluster.local`.** Works but doesn't survive a node replacement.
- **Approach 3 — Cassandra driver's contact-points list including both the headless Service name and the pod FQDN.** Driver resolves the headless Service via DNS SRV records.

#### Final Solution
`spark-streaming-deployment.yaml` passes `--cassandra cassandra-0.cassandra-svc.brainwatch.svc.cluster.local` on the spark-submit command line. Kafka uses the simpler `kafka:9092` because the Bitnami chart exposes a regular ClusterIP. The DLQ catches Kafka publish failures; the producer's `retries=3 max_in_flight=1 acks=all` give an effective circuit-breaker.

#### Key Takeaways
- For StatefulSets, prefer the pod FQDN over the headless Service name as the contact point.
- Always wire a DLQ before integrating — the wrong moment to design a failure path is during the failure.
- Idempotent retries (`acks=all + max_in_flight=1`) > complex backoff logic.

---

### Lesson 6: Performance Optimization — Caching, query opt, resource allocation, bottlenecks

#### Problem Description
- **Context.** The Gold daily rollup reads silver/eeg (millions of rows) multiple times within the same query DAG.
- **Challenges.** Spark recomputed the same filter chain twice when the DAG branched.
- **Impact.** ~2× wall-clock penalty observed on the test fixtures.

#### Approaches Tried
- **Approach 1 — `.cache()` the silver EEG dataframe.** Works in local mode; OOMs in cluster mode for the full 8 GiB dataset.
- **Approach 2 — `.persist(StorageLevel.MEMORY_AND_DISK)`.** Spills to disk under pressure.
- **Approach 3 — keep the single read, restructure the DAG to a single linear chain.** No cache needed; Catalyst plans one scan.

#### Final Solution
We chose approach 3 — the cleanest expression and the smallest memory footprint. The single `joined` DataFrame in `gold_layer.build_patient_features` produces all downstream aggregations from one branch.

#### Key Takeaways
- `.cache()` is the last resort, not the first; restructure the DAG first.
- When you do cache, `MEMORY_AND_DISK` is safer than the default `MEMORY_ONLY`.
- Measure first: `spark.sparkContext.statusTracker` shows how many times a stage is re-executed.

---

### Lesson 7: Monitoring & Debugging — Metrics, alerts, logs

#### Problem Description
- **Context.** The streaming job runs forever; we need to see *why* throughput drops or watermarks stall.
- **Challenges.** Spark UI port 4040 is per-pod and lost across restarts; centralized metrics need a Prometheus path that the capstone doesn't have time to wire.
- **Impact.** Initial debug attempts were `kubectl logs -f` only — slow and ephemeral.

#### Approaches Tried
- **Approach 1 — `kubectl logs --follow`** for the live tail. Works for crashes; useless for steady-state throughput.
- **Approach 2 — Spark UI via `kubectl port-forward svc/spark-streaming-ui 4040:4040`.** Best per-query insight; doesn't persist.
- **Approach 3 — Push `streamingQuery.lastProgress` to a file at every `processingTime` trigger.** Cheap, persistent, scriptable.

#### Final Solution
A small `progress_callback` writes `lastProgress` (JSON) every 30 s to `data/checkpoints/progress.jsonl`; the dashboard reads the tail of that file for rate + watermark drift. For deeper inspection, port-forward to the Spark UI.

#### Key Takeaways
- Streaming logs are necessary but not sufficient — a periodic structured snapshot is more useful.
- Always include the *watermark* timestamp in the snapshot; drift is the earliest sign of a wedged join.
- Don't roll your own Prometheus exporter for a capstone — the file-tail trick gets you 80% of the value.

---

### Lesson 8: Scaling — Horizontal vs vertical, auto-scaling, resource planning

#### Problem Description
- **Context.** A neuro-ICU produces ~70 MB / hour / patient of raw EEG; 50 beds × 24 h = ~84 GB / day. The streaming pipeline must scale linearly with bed count.
- **Challenges.** Local-mode Spark caps at single-pod parallelism; vertical scaling has hard limits at ~16 cores.
- **Impact.** On our single-node demo, the streaming query saturates at ~50 k events/s.

#### Approaches Tried
- **Approach 1 — vertical scale to a 16-core 32 GiB pod.** Linear but expensive past 8 cores.
- **Approach 2 — Spark cluster mode with separate executor pods.** Real horizontal scaling; cluster setup is out of scope for the capstone.
- **Approach 3 — partition the Kafka topics by `patient_id` and run one Spark job per partition-group.** Pseudo-horizontal; simple but rebalance-painful.

#### Final Solution
For the capstone we ship approach 1 (`requests: cpu=1, limits: cpu=2 + memory 4Gi`) and document approach 2 as the production path. The bronze zone is already partitioned by `site_id`, so per-site Spark jobs are a natural future split.

#### Key Takeaways
- Plan the partition key (`site_id`) at ingestion time even if you can't yet split the job — it's the lever for future horizontal scale.
- Vertical scaling is fine for a capstone, but make it obvious where the next-step horizontal lever lives.
- Cluster-mode Spark on Kubernetes needs more memory than local-mode — the executor JVM overhead is non-trivial.

---

### Lesson 9: Data Quality & Testing — Validation, unit / integration / performance

#### Problem Description
- **Context.** A "67 tests passing" claim is meaningless if half the tests are `pass` stubs (we discovered this during finalization).
- **Challenges.** Stub tests pass without proving anything. Real Spark tests need a JVM, which not every developer has installed.
- **Impact.** False confidence: the silver and gold layers had `pass` implementations *and* `pass` tests, so CI was green while the modules were empty.

#### Approaches Tried
- **Approach 1 — mark Spark tests `@pytest.mark.skipif(no pyspark)`.** Tests skip cleanly without Java/Spark.
- **Approach 2 — provide a `conftest.py` that builds one shared SparkSession.** Cleaner; we adopted this implicitly via module-scoped fixtures.
- **Approach 3 — add CI matrix entries with and without pyspark.** Out of scope for the capstone, documented as future work.

#### Final Solution
`tests/test_silver_layer.py` and `tests/test_gold_layer.py` use a module-scoped `SparkSession(local[2])` fixture and a `tmp_path` workspace; each test writes a tiny Parquet fixture, runs the real builder, and asserts an exact-value outcome (count, version, quality flag, broadcast plan presence). Result: **67/67 tests pass with real assertions** when pyspark + Java are installed; 59 still pass cleanly without.

#### Key Takeaways
- Banish `pass` test bodies. A skipped test is honest; an empty test body is a lie.
- Module-scoped Spark fixtures keep test suites under 15 s end-to-end.
- Pin specific values, not just shapes: `assert n_eeg_chunks == 3`, not `assert n_eeg_chunks > 0`.

---

### Lesson 10: Security & Governance — Access control, encryption, audit

#### Problem Description
- **Context.** Clinical data carries HIPAA-equivalent obligations; even synthetic EHR with realistic patterns should not leak credentials or paths.
- **Challenges.** Multiple secrets sources: AWS root key for S3, Kafka bootstrap, Cassandra contact point. Easy to commit one accidentally.
- **Impact.** During the merge sprint we caught one absolute-lab-path commit and one mixed-language comment that hinted at internal infrastructure.

#### Approaches Tried
- **Approach 1 — `.gitignore` for everything secret.** Works for files; doesn't catch inline strings.
- **Approach 2 — Pre-commit hook with `gitleaks`.** Heavier; out of scope.
- **Approach 3 — Documented `BDSP_CREDENTIALS` env-var pattern + secrets-file `rootkey.csv` placed *outside* the repo (`courseworks/credentials/rootkey.csv`).** Credentials never enter `git` by construction.

#### Final Solution
Credentials live at `../credentials/rootkey.csv`, one level above the repo root. `scripts/download_eeg_ehr.py` reads them via `load_aws_credentials()` and explicitly forbids logging them. The deploy script reads no secrets — production K8s would use a `Secret` resource that's not committed.

#### Key Takeaways
- The single most effective control is placing secret files *outside* the repo tree.
- Document the env-var contract in `CLAUDE.md` / `README` so new contributors don't recreate the mistake.
- Audit logs for the alert publisher: every `publish_alerts` call writes one log line — sufficient for capstone post-hoc review.

---

### Lesson 11: Fault Tolerance — Failure recovery, replication, backup

#### Problem Description
- **Context.** A capstone demo cluster is single-replica everywhere (Kafka 1, Cassandra 1, Spark 1). A pod restart should not lose data.
- **Challenges.** Bronze data lives on a PVC that survives pod restart, but a node failure kills it. Spark streaming state lives in `checkpoints-pvc`; deleting the PVC effectively resets the world.
- **Impact.** Two demo dry-runs lost 30 minutes of bronze data after PVC `kubectl delete` without `--keep`.

#### Approaches Tried
- **Approach 1 — single-replica everything + `--keep` on delete.** Best for demo simplicity; depends on human discipline.
- **Approach 2 — replication factor 3 in Cassandra.** Real production move; requires 3 pods, exceeds our resource budget.
- **Approach 3 — daily rsync of bronze + Cassandra snapshot to a backup PVC.** Half-step between (1) and (2); manageable for a capstone.

#### Final Solution
We ship (1) with two hardenings: `teardown.sh --delete-pvcs` double-prompts (`yes` then `DELETE`) before destroying state; the deploy.sh README documents that `kubectl delete pvc` is destructive. The Cassandra StatefulSet uses a `volumeClaimTemplate` so the PVC survives pod replacement.

#### Key Takeaways
- A double-prompt on the destroy path is cheap insurance against human error.
- For a single-node demo, the StatefulSet `volumeClaimTemplate` *is* the fault tolerance.
- Document the recovery procedure (`kubectl rollout restart deployment/spark-streaming`) — the checklist is the runbook.

---

## Appendix A — Reproducing the Demo

```bash
# 1. Environment
source .venv/bin/activate
export JAVA_HOME="$PWD/.javaenv"
export PATH="$JAVA_HOME/bin:$PATH"

# 2. Tests (67/67 passing)
pytest -v

# 3. Generate 8 GiB of synthetic bronze events
python scripts/generate_demo_data_at_scale.py \
       --manifest artifacts/week2/download_manifest.json \
       --bronze data/lake/bronze --target-gb 8 \
       --checkpoint-every 250000

# 4. Batch driver: bronze → silver → gold
python scripts/run_batch.py \
       --bronze data/lake/bronze \
       --silver data/lake/silver \
       --gold   data/lake/gold

# 5. Validate Kubernetes manifests
kubeconform -summary -verbose -ignore-missing-schemas infra/k8s/*.yaml

# 6. Deploy (requires a cluster)
bash infra/k8s/deploy.sh --dry-run    # safety
bash infra/k8s/deploy.sh              # real deploy
```

## Appendix B — Author Contributions

| Member | Owns | Key files |
|---|---|---|
| Quang-Hung | Architecture, code review, speed layer, integration | `speed_layer.py`, `bronze_ingest.py`, `replay_to_kafka.py`, `run_batch.py` |
| Kim-Quan | Batch layer (silver + gold), real-time replay | `silver_layer.py`, `gold_layer.py`, `eeg_replay.py` |
| Kim-Hung | Serving + anomaly rules, EDF Kafka producer | `cassandra_sink.py`, `alert_publisher.py`, `anomaly_rules.py`, `edf_kafka_producer.py` |
| Dat | Kubernetes manifests + deploy/teardown | `infra/k8s/*.yaml`, `deploy.sh`, `teardown.sh` |
| Trang | EHR loader, E2E demo, tests, dashboard | `ehr_loader.py`, `end_to_end_demo.py`, `test_*.py`, `demo_dashboard.py` |

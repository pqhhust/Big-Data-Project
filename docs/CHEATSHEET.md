# BrainWatch · Defense Cheat-Sheet

> One page each side. Print 2-sided. Everything that fits on this paper is what
> you must be able to say from memory. Everything else lives in
> `STUDY-GUIDE.md` / `QA-BANK.md`.

---

## ────────  SIDE A — what BrainWatch is  ────────

### 30-second pitch

> "BrainWatch is a **Lambda-architecture big-data platform** that ingests real
> hospital EEG + EHR, processes them with **Spark** (batch *and* streaming),
> stores alerts in **Cassandra**, and visualises them in **Grafana** — all
> on **AWS EKS**. We run on **17 GiB of real Harvard BDSP EEG** in S3
> (1,571 recordings across 4 sites — 50% pre-loaded into bronze, the rest
> streamed live during the demo)."

### Architecture (whiteboard this in 60 s)

```
 BDSP S3 (EDF) ─► download_real_edf.py ─► local EDF
                                            │
                                            ▼ edf_to_bronze.py  (mne, measured)
 HEEDB ICD-10 ─► build_real_ehr.py ─►   bronze JSONL on PVC
                                            │
                                            ▼ hdfs-bronze-loader Job
                                  ┌─────────────────────────┐
                                  │  HDFS  (NameNode + 2×DN) │
                                  │  /lake/bronze            │
                                  │  /lake/silver  ◄─────────┼─── Spark batch (run_batch.py)
                                  │  /lake/gold              │
                                  │  /checkpoints  ◄─────────┼─── speed-layer Spark
                                  └────────┬─────────────────┘
                                           │
                          ┌────────────────┴────────────────┐
                          ▼                                  ▼
                    BATCH (run_batch on HDFS)          SPEED layer
            silver: dedup + quality_flag         Kafka readStream
            gold:   broadcast(patient_dim) +     → 30s window
                    ±30 min EHR join +           → UDF anomaly score
                    daily rollups                → classify_v2
                          │                      → foreachBatch
                          ▼                      → CASSANDRA  alerts
                  alerts dataset                       │
                          │                            ▼
                          └─────────┬──────► cassandra_to_s3_exporter (3s)
                                    ▼                  │
                            S3 dashboard bucket  ◄─────┘   ← serving-side (survives cluster teardown)
                                    │
                                    ▼
                  Grafana 11  ·  Infinity datasource
                  6 dashboards: Live Alerts · Pipeline · Insights ·
                                Data Explorer · About · **Architecture Status**

Dynamic bronze production: `bronze-streamer` Deployment reads EDFs from
`s3://brainwatch-capstone/raw_edf/` (17 GiB / 1,571 files), parses with mne,
writes JSONL to bronze-pvc on a 20 s tempo. The two CronJobs above keep
HDFS in sync and rebuild silver/gold on each fire.

Cluster-state visibility: `cluster-state-exporter` Deployment runs `kubectl`
+ `hdfs dfsadmin` + `cqlsh` every 30 s and writes flat JSONs to S3
(`cluster_summary.json` etc.) that the Architecture Status dashboard reads.
```

**Hybrid storage:** HDFS is the **compute-side** distributed FS (bronze/silver/gold
+ Spark checkpoints — the rubric's "HDFS or equivalent" box, literal HDFS).
S3 is the **serving-side** object store (rollup JSON for dashboards) — survives
a full cluster teardown for ~$1/mo.

**Dynamic batch:** Two K8s CronJobs fire every 5 min:
`*/5 * * * *` syncs bronze-pvc → HDFS, `2-59/5 * * * *` runs the Spark batch
that rebuilds silver + gold on HDFS. `concurrencyPolicy: Forbid` so they
never overlap. Speed layer is continuous (every 5 s). End-to-end now lives.

**Bronze archive (not just point):** `bronze-streamer` reads EDFs from
`s3://brainwatch-capstone/raw_edf/`, parses with `mne` for the JSONL
features, **and copies the raw EDF binary** into `bronze/edf/` so bronze
size grows with each EDF (capped at `ARCHIVE_RAW_CAP_GIB=4` so HDFS RF=2
stays under 50% of the 40 GiB cluster). Matches real hospital ingest. See
`QA-BANK.md` §17.18.

**Kafka real-stream evidence:** `eeg.raw` + `ehr.updates` carry **1.3 M+
events each** (4 partitions), `kafka-producer` sustains ~333 events/s,
speed-layer's `foreachBatch` writes 60–100 alerts per micro-batch into
Cassandra. **Not a static replay — continuously producing as bronze grows.**

**Pipeline dashboard layout — 6×2 stat grid:**
```
Row 1 (y=3):  Raw EDF on S3 (GiB)  ·  Raw EDF files     ·  Bronze size (GiB)
              Silver size (MB)     ·  Gold size (KB)    ·  Total events
Row 2 (y=8):  EDFs streamed        ·  Alerts (Cassandra) · Bronze→Silver compression
              EKS batch (s)        ·  Generator events/s · Tests passing
Row 3 (y=13): Data-lake zone sizes (live, table+gauge cells) | Stage timings (snapshot)
Row 4 (y=21): Live alert ingestion rate (timeseries)
```

### The numbers (memorize)

| Metric | Value |
|---|---|
| Raw EDF in S3 | **17 GiB · 1,571 recordings · 4 sites (S0001/S0002/I0002/I0003)** |
| Bronze (HDFS, dynamic) | grows continuously, capped at 4 GiB (JSONL features + archived EDF binaries) |
| Silver (HDFS, Parquet) | rebuilt every 5 min by the spark-batch CronJob |
| Gold (HDFS, Parquet) | per-patient daily features, partitioned by event_date |
| Compression (JSONL → Parquet) | ~42× — measured on the silver path |
| Alerts | **100K+ in Cassandra** (steady ~60–100 per micro-batch, every 5 s) |
| Kafka throughput | ~333 events/s sustained (eeg.raw + ehr.updates, 4 partitions each, 1.3M+ events each) |
| Sampling rates | 200 / 256 / 512 Hz (real, measured per EDF) |
| Channel counts | 19–148 (real montages) |
| Real ICD-10 matched | HEEDB neurology table, 28 categories |
| Tests | **131 passing** (Spark + non-Spark, JAVA_HOME from `.javaenv`) |
| K8s resources | ~38 total (HDFS + Kafka + Cassandra + Grafana + 4 long-running Deployments + 2 CronJobs), kubeconform-clean |
| EKS nodes | 2× t3.xlarge in us-east-1d/f |
| HDFS | **1 NameNode + 2 DataNodes**, RF=2, 64 MiB block, ~40 GiB total capacity |
| Batch runtime (EKS) | ~50 s per CronJob fire on the current bronze |
| Paused storage cost | **~$1/month** (5 EBS snapshots + 2 S3 buckets) |

### Rubric compliance ✅

5 mandatory components — all in production:

| Rubric component | Our answer |
|---|---|
| Data processing | **Apache Spark 3.5** (batch + Structured Streaming + MLlib) |
| Distributed storage | **HDFS** (NameNode + 2 DataNodes, RF=2, `bde2020/hadoop:3.2.1`) **+ S3** for serving |
| Message queue | **Apache Kafka 3.9 KRaft** (no ZooKeeper) |
| Database (NoSQL) | **Cassandra 4.1** (wide-column) |
| Deployment | **AWS EKS** (managed Kubernetes on managed cloud) |

11 lesson categories — all in `Reflections.tex` using the four-part
rubric structure (Problem → Approaches → Final Solution → Key
Takeaways):

| # | Lesson | # | Lesson |
|---|---|---|---|
| 1 | Data Ingestion | 7 | Monitoring & Debugging |
| 2 | Data Processing with Spark | 8 | Scaling |
| 3 | Stream Processing | 9 | Data Quality & Testing |
| 4 | Data Storage | 10 | Security & Governance |
| 5 | System Integration | 11 | Fault Tolerance |
| 6 | Performance Optimization | | |

Full requirement → artefact map in `docs/RUBRIC-COVERAGE.md` (also
mirrored in the report as `Requirements.tex` and Appendix 2).

### Spark depth — all 20 sub-items they will grade you on

20 sub-items across 6 categories. **18 are realised in production
code paths or scripts; 2 are out of scope with a stated reason.**

**1. Complex aggregations (4/4)**

| Sub-item | Where |
|---|---|
| Window functions | `silver_layer.build_ehr_silver` — `row_number().over(partitionBy(patient_id, encounter_id).orderBy("version desc"))` |
| Advanced aggregation functions | `gold_layer.build_patient_features` — `F.count`, `F.avg`, `F.max`, `F.first`, `F.collect_set` |
| Pivot / unpivot | `scripts/spark_advanced_demo.py::build_severity_pivot` — alerts pivoted to (site, date) × severity |
| Custom aggregation (UDAF) | `scripts/spark_advanced_demo.py::build_quality_histogram` — grouped-agg `pandas_udf` for per-patient signal-quality histogram |

**2. Advanced transformations (3/3)**

| Sub-item | Where |
|---|---|
| Multi-stage transformations | bronze → silver → gold pipeline; `run_batch.py` chains four Spark functions |
| Chaining complex operations | `gold_layer.build_patient_features` — broadcast join + sort-merge join + groupBy + multi-column agg + partitioned write |
| Custom UDFs | `speed_layer._score` — windowed feature row → 0–1 anomaly score |

**3. Join operations (3/3)**

| Sub-item | Where |
|---|---|
| Broadcast join (small dim) | `F.broadcast(patient_dim)` in `gold_layer`; pinned by `tests/test_gold_layer.py::test_patient_dim_join_uses_broadcast` |
| Sort-merge join (large fact) | silver EEG ⋈ silver EHR on `patient_id` within ±30-min predicate in `gold_layer.build_patient_features` |
| Multi-join optimisation | broadcast hint + AQE + `spark.sql.shuffle.partitions` → single shuffle stage in the post-AQE plan |

**4. Performance optimisation (3/4 + 1 out of scope)**

| Sub-item | Where |
|---|---|
| Partition pruning | silver writes `partitionBy("site_id", "ingestion_date")` |
| Caching / persistence | `scripts/spark_advanced_demo.py::demonstrate_caching` calls `df.cache()` and prints the post-cache `explain(mode="formatted")` |
| Query optimisation / `explain` | `spark.sql.adaptive.enabled=true` on every job; broadcast hint pinned at the call site; plan inspected in regression test |
| Bucketing | **Out of scope** — requires `saveAsTable` against a Hive metastore; partitioned-Parquet layout already gives per-site pruning |

**5. Stream processing (6/6)**

Two speed-layer queries run **concurrently** via `speed_layer.main()
--mode=both`: `build_kafka_streaming_pipeline` (`source='speed_lookup'`,
Cassandra patient_state lookup, p50 ≈ 12 s) and
`build_kafka_join_pipeline` (`source='speed_join'`, true Kafka
stream-stream join, ≈ 60 s emission). Both score with the canonical
`anomaly_rules.compute_anomaly_score`.

| Sub-item | Where |
|---|---|
| Structured Streaming | both `build_kafka_streaming_pipeline` (lookup) and `build_kafka_join_pipeline` (stream-stream join) |
| Output modes | production: `outputMode("append")` on both queries; legacy `build_streaming_pipeline` (Parquet source) retains `outputMode("update")` for documentation |
| Watermarking | lookup: `withWatermark("event_time", "30 seconds")` on EEG; join: 30 s EEG + 30 min EHR; predicate is `±30 min` between the two event-time columns |
| Late data handling | records past the watermark dropped (append-mode); DLQ catches malformed records before the stream |
| State management | windowed aggregation + join state on `checkpoints-pvc` (lookup: `/kafka_speed_layer`; join: `/kafka_speed_join`); both survive pod restart via checkpoint replay |
| Exactly-once guarantees | replayable Kafka source + idempotent Cassandra PK + checkpointed state. Empirical check: `scripts/verify_exactly_once.sh` |

**6. Advanced analytics (2/3 + 1 out of scope)**

| Sub-item | Where |
|---|---|
| Machine learning (MLlib) | `scripts/train_severity_model.py` — `LogisticRegression` over `VectorAssembler` of gold features; reports AUC on a held-out split |
| Graph processing (GraphFrames) | **Out of scope** — the domain has no natural graph relation; relevant relations are temporal (window) and dimensional (broadcast) |
| Statistical / time series | `speed_layer` windowed time-series agg; analytics scripts compute diurnal patterns and per-site severity time series |

### Anomaly score (know by heart)

```
score = 0.30·chunk_term + 0.25·quality_term
      + 0.30·critical_term + 0.15·meds_term         (clamped 0..1)

chunk_term    = min(eeg_chunk_count / 60.0, 1.0)
quality_term  = 1.0 - signal_quality_score
critical_term = 0.6 if has_critical_lab else 0.0
meds_term     = min(n_medication_changes_24h / 5.0, 1.0)
```

```
classify_v2:
  critical_lab AND score ≥ 0.60   →  critical   (fast path)
  score ≥ 0.85                    →  critical
  score ≥ 0.65                    →  warning
  score ≥ 0.40                    →  advisory
  else                            →  normal

v1 quality gate (still used by speed layer UDF):
  signal_quality < 0.3            →  suppressed   (FIRST check)
```

### Cassandra schema

```sql
CREATE TABLE brainwatch.alerts (
  patient_id     text,
  alert_time     timestamp,
  severity       text,
  anomaly_score  float,
  explanation    text,
  PRIMARY KEY (patient_id, alert_time)
) WITH CLUSTERING ORDER BY (alert_time DESC);
```
Partition by `patient_id` → all of a patient's alerts on one node.
Clustering `DESC` → newest-first read without ORDER BY.

---

## ────────  SIDE B — defending it live  ────────

### Top 12 Q&A (one-line answers)

1. **Why Lambda not Kappa?** — Cheap historical reprocessing of cold BDSP
   data; Kappa would replay the whole stream. Marz & Warren; Lin (2017).
2. **Why Kafka in KRaft mode?** — No ZooKeeper → one fewer stateful service.
   Apache deprecated ZK in 3.5+.
3. **Why Parquet for silver/gold?** — Columnar + Snappy, ~42× smaller than
   JSONL, predicate/column pushdown, splittable.
4. **Why Cassandra for alerts?** — Write-heavy, masterless, partition by
   `patient_id` → linear write scaling and O(1) per-patient reads.
5. **What's the anomaly score?** — `0.30·chunk + 0.25·quality + 0.30·critical
   + 0.15·meds`, clamped 0..1; critical-lab fast path at 0.60.
6. **How does the watermark bound state?** — Watermark = max event-time − 30 s
   lateness; window state older than watermark is evicted; late events past it
   are dropped.
7. **Why broadcast join in gold?** — `patient_dim` is small → broadcast it →
   no shuffle → orders of magnitude faster. Asserted in a test.
8. **Why doesn't EDF stream through Kafka?** — EDF blobs are 10s–100s of MB.
   You never push that through a bus; binary stays in the lake, events carry a
   `source_uri` + measured features.
9. **How is exactly-once handled?** — Replayable source (Kafka offsets) +
   idempotent sink (Cassandra PK upsert) + checkpointed state on HDFS.
10. **What about the stream-stream join?** — We ship it. The speed layer runs
    **two** queries concurrently: `build_kafka_streaming_pipeline` does a
    Cassandra `patient_state` lookup per micro-batch (p50 ≈ 12 s, tagged
    `source='speed_lookup'`); `build_kafka_join_pipeline` does the canonical
    Kafka EEG⋈EHR stream-stream join with append-mode + windowed agg
    (≈ 60 s emission, tagged `source='speed_join'`). The dashboard's
    Real-Time Alerts panel filters on `source` to show both side by side.
    The append-mode + windowed-agg latency is real but not a deal-breaker
    when the lookup query is the live-demo headline.
11. **Why hybrid HDFS + S3?** — HDFS is the **compute-side** distributed FS
    (bronze/silver/gold + Spark checkpoints, RF=2, NameNode UI demo). S3 is the
    **serving-side** so the dashboard survives a cluster teardown for ~$1/mo.
    Best of both rubrics.
12. **Why RF=2 not RF=3?** — Two DataNodes is the smallest cluster that
    survives one node failure (the canonical "distributed storage" property).
    RF=3 would need 3 DNs — same demo story, +50% cost.

### The four V's (rubric)

- **Volume:** 306,741 recordings · 115,060 unique subjects · 3.2M valid hours
  (full BDSP); **17 GiB / 1,571 recordings** in our S3-staged cohort.
- **Velocity:** sub-minute ingestion-to-alert; 30 s window / 15 s slide.
- **Variety:** EDF time-series · structured EHR · per-site CSV metadata with
  schema differences (`DurationInSeconds` vs `RecordingDuration`).
- **Veracity:** 11,579 rows missing duration (3.8%), 427 ultra-short sessions,
  schema heterogeneity → handled by a fallback chain + DLQ.

### Kafka topics

| Topic | Producer | Consumer |
|---|---|---|
| `eeg.raw` | `kafka_producer_driver.py` | speed-layer, bronze ingest |
| `ehr.updates` | EHR loader | speed-layer, bronze ingest |
| `features.realtime` | speed-layer | serving |
| `alerts.anomaly` | serving | dashboard / notifications |

### Files to have open during the demo

```
src/brainwatch/contracts/events.py            ← schemas
src/brainwatch/serving/anomaly_rules.py       ← score + classify_v2
src/brainwatch/processing/speed_layer.py      ← streaming heart
src/brainwatch/processing/silver_layer.py     ← dedup + quality_flag
src/brainwatch/processing/gold_layer.py       ← broadcast + ±30min join
infra/cloud/k8s-overlays/real-pipeline.yaml   ← the 4 cloud components
```

### Live demo runbook (7 minutes)

```
0:00  Architecture picture (Side A).
0:30  kubectl -n brainwatch get pods                          → all Running
1:00  Grafana · Live Alerts                                   → severities tick up
2:00  Grafana · Pipeline                                      → 7 min batch, 42× compression
3:00  Grafana · Clinical Insights                             → real HEEDB ICD-10
4:00  Grafana · Data Explorer                                 → bronze→silver→gold same row
5:00  scripts/add_note.py --layer silver --text "..."         → notes panel updates
6:00  pytest -q                                               → 131 passing
6:30  Costs: ~$0.40/h running, ~$1/mo paused, resume 15 min
```

### Cost / pause / resume

```
running:  ~$0.40/h    EKS + 2× node + EBS + NAT
paused:   ~$1/month   5 EBS snapshots (used-blocks only) + 2 S3 buckets
resume:   ~15-20 min  bash infra/cloud/resume_from_snapshots.sh
                      (reads artifacts/eks/snapshots/index.txt)
```

### If something goes wrong on stage

| Symptom | What to do |
|---|---|
| Live dashboard slow / blank | Switch to **S3 static dashboard URL** — it serves the last snapshot, no cluster needed: `http://brainwatch-dashboard-923884399064.s3-website-us-east-1.amazonaws.com` |
| Pods CrashLoopBackOff | `kubectl logs ... --previous` — most common cause is the spark image's read-only `/home/spark`; fix is already in `real-pipeline.yaml` (`pip --target=/code/site-packages`). |
| Stream-stream join in shipped code | `build_kafka_join_pipeline` is live alongside `build_kafka_streaming_pipeline`. Append-mode + 1-min window means ≈ 60 s emission delay — not a deal-breaker; that's why the lookup query is the headline and the join is the accuracy benchmark. Both write to `brainwatch.alerts` tagged via the `source` column. |
| Q you don't know | "Good question — that's covered in `docs/QA-BANK.md` §X — short version: <one sentence>." Honest beats fabricated. |

### Phrases that score points

- "Lambda lets us **reprocess** the 17 GiB cold corpus cheaply — Kappa would
  re-stream it."
- "We pay the **broadcast** so we don't pay the **shuffle**."
- "Watermark bounds **state**; the window bounds **emission**."
- "Cassandra is **AP**; we tolerate transient under-replication for write
  throughput. The batch layer reconciles."
- "The dashboard depends on **S3 only** — that's why we can tear the cluster
  down and still demo."

### Phrases that lose points (avoid)

- "We **simulated** the EEG data." *(No — EDFs are real BDSP recordings.)*
- "The score is a **machine-learning model**." *(It's a weighted sum + MLlib
  LR is trained but the live UDF is the rule-based score.)*
- "Spark runs **in real time**." *(Micro-batch, not record-at-a-time.)*
- "We use **MapReduce**." *(We use Spark; MapReduce is the conceptual ancestor.)*

---

*Source of truth: `docs/STUDY-GUIDE.md` for everything, `docs/QA-BANK.md` for the
full question bank, `docs/PRESENTATION-GUIDE.md` for the longer pitch.*

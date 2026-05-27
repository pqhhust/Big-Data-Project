# IT4043E rubric coverage

Authoritative map from every requirement in
`html/IT4043E - Big data storage and processing _ Viet-Trung Tran.html`
to the BrainWatch artefact that satisfies it: a file and function for
code, a manifest path and resource kind for Kubernetes objects, a test
file and function for regression tests, and the report chapter/section
where the requirement is discussed in prose.

The pairing is fine-grained enough that any single rubric item can be
confirmed against the repository without running the system
end-to-end. Status legend:

- **Used** — appears in production code paths (silver, gold, speed
  layer, or the analytics demo).
- **Demo** — a dedicated script under `scripts/` exercises the
  technique.
- **Out of scope** — the technique was not used; the reason is
  recorded inline.

---

## I. Technical requirements (5 mandatory components)

| Component | Technology | Where (code / manifest) | Report § |
|---|---|---|---|
| Data processing | Apache Spark 3.5 (batch + Structured Streaming + MLlib) | `src/brainwatch/processing/`, `scripts/train_severity_model.py` | Benchmarking §4 |
| Distributed storage | HDFS (1 NameNode + 2 DataNodes, RF=2) **+** Amazon S3 hybrid | `infra/cloud/k8s-overlays/hdfs.yaml` | Benchmarking §2, Background §HDFS |
| Message queue | Apache Kafka 3.9 in KRaft mode | `infra/cloud/k8s-overlays/kafka-kraft.yaml` | Benchmarking §3, Background §Kafka |
| Database (NoSQL) | Apache Cassandra 4.1 | `infra/k8s/cassandra-statefulset.yaml`, `src/brainwatch/serving/cassandra_sink.py` | Benchmarking §5, Background §NoSQL & CAP |
| Deployment | AWS EKS 1.30 (managed Kubernetes on managed cloud) | `infra/cloud/deploy_cloud.sh`, `infra/cloud/resume_from_snapshots.sh` | Benchmarking §6 |

The rubric is explicit that *Docker alone is not encouraged*. Local
development uses `docker compose` for convenience; the deployment
target throughout the report is Kubernetes on managed cloud.

---

## II. Spark proficiency (20 sub-items across 6 categories)

### 1. Complex aggregations (4 items)

| Sub-item | Status | Where |
|---|---|---|
| Window functions | Used | `silver_layer.build_ehr_silver` — `row_number()` over `(patient_id, encounter_id)` ordered by `version desc` |
| Advanced aggregation functions | Used | `gold_layer.build_patient_features` — `F.count`, `F.avg`, `F.max`, `F.first`, `F.collect_set` |
| Pivot / unpivot | Demo | `scripts/spark_advanced_demo.py::build_severity_pivot` — pivots alerts into a wide table (rows = (site, date), columns = severities) |
| Custom aggregation (UDAF) | Demo | `scripts/spark_advanced_demo.py::build_quality_histogram` — grouped-agg `pandas_udf` for a fixed-bin signal-quality histogram per patient |

### 2. Advanced transformations (3 items)

| Sub-item | Status | Where |
|---|---|---|
| Multi-stage transformations | Used | bronze → silver → gold pipeline; `run_batch.py` chains four Spark functions |
| Chaining complex operations | Used | `gold_layer.build_patient_features` — broadcast join + sort-merge join + groupBy + multi-column aggregation + partitioned write |
| Custom UDFs | Used | `speed_layer._score` Python UDF producing the 0–1 anomaly score from the windowed feature row |

### 3. Join operations (3 items)

| Sub-item | Status | Where |
|---|---|---|
| Broadcast join (small dim) | Used | `F.broadcast(patient_dim)` in `gold_layer`; pinned by `tests/test_gold_layer.py::test_patient_dim_join_uses_broadcast` |
| Sort-merge join (large fact) | Used | silver EEG joined to silver EHR within ±30-min predicate in `gold_layer.build_patient_features` |
| Multi-join optimisation | Used | broadcast hint + AQE + explicit `spark.sql.shuffle.partitions` → one shuffle stage in the post-AQE plan |

### 4. Performance optimisation (4 items)

| Sub-item | Status | Where |
|---|---|---|
| Partition pruning | Used | silver writes `partitionBy("site_id", "ingestion_date")` |
| Caching / persistence | Demo | `scripts/spark_advanced_demo.py::demonstrate_caching` calls `df.cache()` and prints the post-cache `explain(mode="formatted")` |
| Query optimisation / `explain` | Used | `spark.sql.adaptive.enabled=true` on every Spark job; broadcast hint pinned at the call site; plan inspected in regression test |
| Bucketing | Out of scope | Requires `saveAsTable` against a Hive metastore. The cohort and the partitioned-Parquet layout already give per-site partition pruning; there is no workload where bucketing would beat partition pruning. Stated explicitly in the report. |

### 5. Stream processing (6 items)

| Sub-item | Status | Where |
|---|---|---|
| Structured Streaming | Used | Two concurrent queries via `speed_layer.main() --mode=both`: `build_kafka_streaming_pipeline` (Cassandra-lookup, `source='speed_lookup'`, p50 ≈ 12 s) and `build_kafka_join_pipeline` (Kafka stream-stream join, `source='speed_join'`, ≈ 60 s emission) |
| Output modes | Used | Both production queries: `outputMode("append")`. Legacy `build_streaming_pipeline` (Parquet source) retains `outputMode("update")` |
| Watermarking | Used | Lookup: `withWatermark("event_time", "30 seconds")` on EEG. Join: 30 s on EEG + 30 min on EHR; ±30-min event-time predicate on the stream-stream join |
| Late data handling | Used | Records past the watermark dropped (append-mode); bronze writer's DLQ catches malformed records before the stream |
| State management | Used | Windowed agg + join state persisted to `checkpoints-pvc` (lookup: `/kafka_speed_layer`, join: `/kafka_speed_join`); both queries survive pod restart via checkpoint replay |
| Exactly-once guarantees | Used | Replayable Kafka source + idempotent Cassandra PK + checkpointed Spark state. Empirical check: `scripts/verify_exactly_once.sh` |

### 6. Advanced analytics (3 items)

| Sub-item | Status | Where |
|---|---|---|
| Machine learning (MLlib) | Used | `scripts/train_severity_model.py` fits `LogisticRegression` over a `VectorAssembler` of the gold features and reports AUC on a held-out split |
| Graph processing (GraphFrames) | Out of scope | Our domain has no natural graph relation among entities; the relevant relations are temporal (window) and dimensional (broadcast). We did not fabricate a graph to fit the category. |
| Statistical computations / time series | Used | `processing/eeg_features.py` extracts windowed signal-processing features per EEG window: band powers (delta/theta/alpha/beta/gamma), Hjorth parameters (activity/mobility/complexity), line-length, spectral entropy. `scripts/extract_eeg_features.py` runs it locally over an EDF via MNE; the gold/batch path picks up the same function via a Pandas UDF. 16 unit tests pin the math (`tests/test_eeg_features.py`). Plus: `speed_layer` windowed time-series aggregation; analytics scripts compute diurnal patterns and per-site severity time series; `spark_advanced_demo` computes per-patient histograms. |

**Two out-of-scope sub-items**, both with a stated reason: bucketing
(no workload benefit) and GraphFrames (no graph in the domain). The
remaining **18 out of 20 sub-items** are realised in production code
paths or in a dedicated `scripts/` demonstration.

---

## III. Report requirements (4 mandatory sections)

| Rubric section | Chapter | Notes |
|---|---|---|
| Problem Definition | `Context.tex` (Chapter 2 of the report) | Motivation, gap, contributions, course context, problem statement, design constraints |
| Architecture and Design | `Benchmarking.tex` (Chapter 5) + `Prototyping.tex` (Chapter 6) | Per-layer choice + alternative declined; deployed topology + module layout + data flow diagrams |
| Implementation Details | `Prototyping.tex` (Chapter 6) | Section-by-section walk through bronze streamer, batch path on HDFS, speed path, serving + exporters, cluster-state visibility, deployment manifests, reproducibility |
| Lessons Learned | `Reflections.tex` (Chapter 9) | One section per rubric lesson, using the prescribed four-part structure (Problem Description → Approaches Tried → Final Solution → Key Takeaways) |

---

## IV. Lessons Learned (11 categories)

Each of the eleven sections in `Reflections.tex` uses the four-part
structure prescribed by the rubric.

| # | Lesson | Anchor in code / manifest |
|---|---|---|
| 1 | Data Ingestion | `scripts/bronze_stream_from_s3.py` + `src/brainwatch/ingestion/bronze_writer.py` (SHA-256 dedup, DLQ routing, validation) |
| 2 | Data Processing with Spark | `src/brainwatch/processing/silver_layer.py`, `gold_layer.py`; broadcast hint + AQE + `spark.sql.shuffle.partitions=16` |
| 3 | Stream Processing | `src/brainwatch/processing/speed_layer.py::build_kafka_streaming_pipeline` (lookup) + `::build_kafka_join_pipeline` (stream-stream join), run concurrently via `main() --mode=both`; pod-delete test in `scripts/verify_exactly_once.sh` |
| 4 | Data Storage | `infra/cloud/k8s-overlays/hdfs.yaml` (HDFS RF=2) + `infra/cloud/k8s-overlays/batch-on-hdfs.yaml` (Parquet partition layout) |
| 5 | System Integration | `infra/cloud/k8s-overlays/batch-on-hdfs.yaml` (post-`-put` HDFS assertion in `hdfs-bronze-loader`) |
| 6 | Performance Optimization | `Empirical.tex` §4 (Spark batch fixed-overhead model: ~80% startup + packages on every fire) |
| 7 | Monitoring & Debugging | `scripts/cluster_state_to_s3.py` + the Architecture Status Grafana dashboard |
| 8 | Scaling | Producer rate ladder + Kafka KRaft single-broker → three-broker production posture |
| 9 | Data Quality & Testing | `tests/` (110 test functions, ~130 collected cases with parameterised tests; see Appendix 2) |
| 10 | Security & Governance | `credentials/rootkey.csv` lives outside the repo working tree; `git log` audit returns no real AKIA key |
| 11 | Fault Tolerance | `infra/cloud/resume_from_snapshots.sh` (one-command bring-up); 8 EBS snapshots in `artifacts/eks/snapshots/index.txt` |

---

## V. Verification artefacts

| Artefact | Purpose |
|---|---|
| `tests/` | 110 test functions across 24 files; expand to >130 cases with `@pytest.mark.parametrize`. Runnable with `pytest -q` in under twenty seconds. Spark-dependent tests skip silently if PySpark is not installed. Full enumeration in Appendix 2 of the report. |
| `infra/cloud/k8s-overlays/` | Deployment manifests (HDFS, Kafka, the real pipeline, the batch overlay, the bronze streamer, the cluster-state exporter). Every file is validated by `kubeconform` before `kubectl apply`. |
| `scripts/spark_advanced_demo.py` | Pivot, persistence, and custom-UDAF demonstrations for the rubric sub-items that do not live on the production code paths. |
| `scripts/train_severity_model.py` | Spark MLlib end-to-end: split, `VectorAssembler`, `LogisticRegression`, AUC, model save. |
| `scripts/ablate_anomaly_hyperparams.py` | Reproducible ablation harness for the three anomaly-scoring hyperparameter studies in `Empirical.tex`. |
| `scripts/verify_exactly_once.sh` | Pod-delete exactly-once empirical check (cited by Lessons 3 and 11). |
| `artifacts/eks/snapshots/index.txt` | Canonical PVC → volume → snapshot-id mapping. |
| `infra/cloud/resume_from_snapshots.sh` | One-command cluster bring-up from the EBS snapshots; measured resume time approximately twenty minutes. |
| `https://github.com/pqhhust/Big-Data-Project` | Public GitHub repository; full commit history with no rebase or squash, so every design reversal is its own commit. |

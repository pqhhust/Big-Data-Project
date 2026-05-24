# BrainWatch — Presentation & Defense Guide

Everything you need to understand to present this project confidently and
answer questions. Read top-to-bottom once; the **"If they ask"** boxes are
your defense cheat-sheet.

---

## 0. The 30-second pitch

> "BrainWatch is a Lambda-architecture big-data platform that ingests real
> hospital EEG recordings and EHR data, processes them with Spark (both batch
> and streaming), stores alerts in Cassandra, and visualizes them in Grafana —
> all deployed on Kubernetes (AWS EKS). We run it on **17 GiB of real EEG**
> from Harvard's BDSP corpus across 1,571 recordings and 4 hospital sites (50% pre-loaded into bronze, the rest streamed live)."

---

## 1. Background knowledge you must own

### 1.1 The problem domain
- **EEG (electroencephalography)** = brain electrical activity, recorded as
  multi-channel time-series (e.g., 19–50 channels, 200–512 Hz). Standard file
  format: **EDF (European Data Format)** — a binary container of the raw
  samples + a header (channels, sampling rate, duration).
- **EHR (electronic health record)** = clinical events: vital signs, lab
  results, medications, diagnoses (coded with **ICD-10**).
- Clinical need: in a neuro-ICU a nurse can't watch 50 live EEG streams; we
  auto-flag anomalies and combine them with clinical context.

### 1.2 Big-data concepts (the rubric)
- **Lambda Architecture** (Nathan Marz): two paths over the same data —
  a **batch layer** (accurate, recomputed) and a **speed layer** (fast,
  approximate), merged at serving. Contrast **Kappa** (stream-only).
  *We chose Lambda because we need historical backfill + reprocessing, which
  Kappa makes expensive.*
- **Medallion / bronze-silver-gold** data-lake pattern:
  - **Bronze** = raw landed events (immutable, append-only). We store JSONL.
  - **Silver** = cleaned, deduplicated, conformed. We use Parquet.
  - **Gold** = business-ready aggregates. Parquet, partitioned.
- **Why Parquet** = columnar + compressed (~42× smaller than our JSONL bronze),
  predicate/column pushdown, splittable for parallel reads.

### 1.3 The technologies
| Tool | One-liner you should be able to say |
|---|---|
| **Apache Kafka** | Distributed, partitioned, replayable commit-log; decouples producers from consumers. We run it in **KRaft mode** (no ZooKeeper). |
| **Apache Spark** | Distributed compute on RDD/DataFrame; lazy DAG, Catalyst optimizer. We use **Structured Streaming** (micro-batch) + batch SQL. |
| **Cassandra** | Wide-column NoSQL, masterless, tunable consistency, fast writes; partition key + clustering key design. |
| **Kubernetes / EKS** | Container orchestration; Deployments (stateless), StatefulSets (stateful: Kafka/Cassandra), PVCs (EBS), Jobs/CronJobs. |
| **Grafana** | Dashboarding; we feed it via the Infinity datasource reading JSON from S3. |

---

## 2. The architecture (draw this on the whiteboard)

```
 REAL EDF (Harvard BDSP S3, credentialed access point)
        │  download_real_edf.py  (metadata-driven, breadth-first)
        ▼
 data/raw/eeg/*.edf  (17 GiB raw archive on S3 + dynamic bronze, 1,571 recordings, 4 sites)
        │  edf_to_bronze.py  (mne reads real signal → measured features)
        ▼
 ┌─ BRONZE ── eeg events (JSONL): channel_count, sampling_rate, window,
 │            mean_amplitude_uv, flat_channel_frac, signal_quality_score
 │            ehr events (JSONL): real HEEDB ICD-10 categories
 │                                              │
 │  ┌───────────────────────────────────────────┴───────────────┐
 │  │ KAFKA  (eeg.raw / ehr.updates)  ← kafka_producer_driver     │
 │  └───────────────────────────────────────────┬───────────────┘
 │                                               ▼
 │                              SPEED LAYER (Spark Structured Streaming)
 │                              readStream.format("kafka") → watermark →
 │                              30s window → UDF score → classify_v2 →
 │                              foreachBatch → Cassandra alerts
 │                                               │
 ├─ BATCH (run_batch.py / CronJob)               ▼
 │   silver: dedup + row_number() latest      CASSANDRA  alerts table
 │           version + quality_flag           (PK patient_id, alert_time DESC)
 │   gold:   broadcast(patient_dim) join +       │
 │           ±30min EHR join + daily rollups     ▼
 │                                          cassandra_to_s3_exporter
 │                                               │  (every 3s → S3 JSON)
 ▼                                               ▼
 ANALYTICS (extract_clinical_insights.py)   GRAFANA (4 dashboards, Infinity DS)
 ICD breakdown / site / diurnal / cohort    Live Alerts · Pipeline · Architecture
 + MLlib LogisticRegression                 · Clinical Insights
```

---

## 3. Spark features demonstrated (they WILL ask "where's the Spark depth")

| Rubric item | File · function | What to say |
|---|---|---|
| Window functions | `silver_layer.build_ehr_silver` | `row_number().over(partitionBy(patient_id,encounter_id).orderBy(version desc))` keeps the latest EHR version |
| Broadcast join | `gold_layer.build_patient_features` | `F.broadcast(patient_dim)` — small dim, avoids a shuffle; **asserted in a test** |
| Sort-merge join | `gold_layer` | large EEG⋈EHR on patient_id + ±30 min predicate |
| Structured Streaming | `speed_layer.build_kafka_streaming_pipeline` | `readStream.format("kafka")`, append output mode |
| Watermark / late data | same | `withWatermark("event_time","30 seconds")` |
| Windowed aggregation | same | `F.window(event_time,"30 seconds","15 seconds")` + count/avg/max |
| Custom UDF | same | `compute_anomaly_score` blend → 0–1 score |
| Partition pruning | `silver_layer` write | `partitionBy("site_id","ingestion_date")` |
| MLlib | `train_severity_model.py` | LogisticRegression + VectorAssembler + AUC eval |

---

## 4. Code tour (open these during the demo)

1. `src/brainwatch/contracts/events.py` — the data contracts (EEGChunkEvent, EHREvent, AlertEvent).
2. `scripts/download_real_edf.py` — how we pull real EDF from BDSP using the access point + metadata CSVs.
3. `scripts/edf_to_bronze.py` — `_quality()` computes **measured** signal quality from the real waveform (mne `get_data`).
4. `src/brainwatch/processing/silver_layer.py` / `gold_layer.py` — the batch transforms.
5. `src/brainwatch/processing/speed_layer.py` — `build_kafka_streaming_pipeline`, the streaming heart.
6. `src/brainwatch/serving/anomaly_rules.py` — `compute_anomaly_score` + `classify_v2` (5-tier severity).
7. `src/brainwatch/analytics/heedb.py` — joins the **real** HEEDB ICD-10 neurology table.
8. `infra/cloud/k8s-overlays/real-pipeline.yaml` — the 4 cluster components.

---

## 5. The numbers (memorize these)

| Metric | Value |
|---|---|
| Real EDF | 17 GiB raw EDF on S3 · 1,571 recordings · 4 sites (S0001/S0002/I0002/I0003) |
| Bronze events | grows continuously as the streamer feeds (currently ~6.8 M events derived from 50% of EDFs) |
| Real sampling rates | 200 / 256 / 512 Hz (measured) |
| Real channel counts | 19–148 (real montages) |
| Silver | 24,759 EEG rows · ~42× Parquet compression |
| Real ICD-10 | HEEDB neurology table, 28 categories matched |
| Alerts | 1,077 (real classify_v2 output) |
| Tests | 131 passing (local-first) |
| K8s | 31 resources, kubeconform-clean; deployed on EKS (2× t3.xlarge) |
| Batch runtime | <10 s local on the real cohort |

---

## 6. If they ask… (anticipated Q&A)

**Q: Is this real medical data?**
Yes — EDF waveforms are real BDSP/Harvard recordings via the credentialed
access point; ICD-10 diagnoses come from the real HEEDB neurology table. The
*anomaly-score variance* term and the synthetic-EHR vitals are simulated; we
say so explicitly in the report (§ honesty).

**Q: Why Lambda not Kappa?**
Historical backfill + reprocessing of research cohorts is cheap in Lambda
(re-run the batch job) and expensive in Kappa (replay the whole stream). See
Lin (2017), "The Lambda and the Kappa", IEEE Internet Computing.

**Q: Why doesn't EDF stream through Kafka?**
EDF blobs are 10s–100s of MB. You never push that through a message bus; the
binary stays in the lake and events carry a `source_uri` reference + measured
features. Standard pattern.

**Q: How is exactly-once handled in the speed layer?**
Three legs: replayable source (Kafka offsets), idempotent sink (Cassandra
`PRIMARY KEY (patient_id, alert_time)` → upsert), checkpointed state (Spark
checkpoint dir on a PVC).

**Q: Stream-stream join?**
Spark requires append output mode for stream-stream joins, which combined with
windowed aggregation imposes a watermark+window delay unsuited to a live demo,
so the live path is EEG-windowed and EHR enrichment happens in the batch/gold
join. This is documented as a real lesson learned.

**Q: How do you scale?**
Bronze is partitioned by `site_id`, so per-site Spark jobs are the horizontal
lever. EKS managed node group autoscales 2→3. Kafka partitions by patient_id.

---

## 7. Links / references
- BDSP (Brain Data Science Platform): https://bdsp.io
- EDF format: https://www.edfplus.info
- mne (EEG in Python): https://mne.tools
- Lambda Architecture: Marz & Warren, *Big Data* (Manning); Lin (2017) IEEE Internet Computing 21(5)
- Spark Structured Streaming guide: https://spark.apache.org/docs/latest/structured-streaming-programming-guide.html
- Cassandra data modeling: https://cassandra.apache.org/doc/latest/cassandra/data_modeling/
- Repo: https://github.com/pqhhust/Big-Data-Project

---

## 8. Live demo runbook (what to click)
1. Show `pytest -q` → 131 passing.
2. `git log --oneline` → show the team's commits by role.
3. Open Grafana → **Live Alerts** (severity timeline), **Clinical Insights**
   (real ICD-10 prevalence), **Pipeline** (real metrics), **Architecture**.
4. `kubectl -n brainwatch get pods` → all Running (producer, speed-layer,
   cassandra, kafka, exporter, grafana).
5. `kubectl -n brainwatch logs deploy/speed-layer | grep foreachBatch` →
   show alerts being written to Cassandra in real time.
6. `cqlsh -e "SELECT * FROM brainwatch.alerts LIMIT 5"` → real rows.

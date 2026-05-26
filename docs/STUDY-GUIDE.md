# BrainWatch — Study Guide

**Goal of this document:** take you from "I have never opened this repo" to
"I can answer any question about it, run it end-to-end, and deploy it to AWS."

This is the *one* file you read first. Every other doc in `docs/` is referenced
from here in the order you should open it.

> 📋 **Rubric coverage map:** `docs/RUBRIC-COVERAGE.md` — every
> IT4043E requirement (5 mandatory components, 20 Spark sub-items, 11
> lesson categories, 4 report sections) mapped to the file, test, and
> report section that satisfies it.

> ⏱ Budget: ~6 hours to read everything below + skim the linked code.
> If you only have 90 minutes before a defense, jump to §11 (Demo Runbook) and
> §12 (Defense Q&A) — they're self-contained.

---

## Table of contents

1. [How to use this guide](#1-how-to-use-this-guide)
2. [Prerequisite materials (what to learn, where)](#2-prerequisite-materials)
3. [The problem domain — EEG, EHR, ICD-10](#3-problem-domain)
4. [Architecture in one picture (and the *why* of every box)](#4-architecture)
5. [Repo tour — where everything lives](#5-repo-tour)
6. [Code walk — module by module, in data-flow order](#6-code-walk)
7. [Running it locally (the 30-minute path)](#7-running-locally)
8. [Running it on Kubernetes — local cluster](#8-running-on-k8s-local)
9. [Running it on AWS EKS — cloud, real data](#9-running-on-eks)
10. [The test suite (110+ tests)](#10-tests)
11. [Demo runbook for the defense](#11-demo-runbook)
12. [Defense Q&A — the questions you *will* be asked](#12-defense-qa)
13. [Cost, teardown, and resume from snapshots](#13-cost-teardown-resume)
14. [Glossary](#14-glossary)
15. [Cross-doc map](#15-cross-doc-map)

---

## 1. How to use this guide

The doc tree of this repo is dense (`final-report.md` is 47 KB alone). Read in
this order, and don't try to memorize anything — the point is to know
**where to look**:

| Order | Read | Why |
|---|---|---|
| 1 | **This file (`STUDY-GUIDE.md`)** | Mental model + map. |
| 2 | `docs/architecture.md` | The big picture in 8 KB. |
| 3 | `docs/TECHNOLOGY.md` | Each technology, why we chose it, what it does in our pipeline. |
| 4 | `docs/PRESENTATION-GUIDE.md` | Pitch + "If they ask" defense cheat-sheet. |
| 5 | `docs/final-report.md` | The formal write-up (lessons learned, results, the rubric). |
| 6 | `docs/setup-guide.md` | Environment install for your laptop. |

Code is best read in **data-flow order** (download → bronze → silver → gold →
stream → serve → dashboard); §6 walks you through that.

---

## 2. Prerequisite materials

You do **not** need to master each of these — you need a working mental model
and one good link per topic so you can re-look-up under pressure.

### 2.1 Lambda architecture (5 min)
- **Concept:** two parallel paths over the same data — a slow, accurate
  **batch layer** and a fast, approximate **speed layer** — merged at the
  **serving layer**.
- Read: ["Lambda Architecture" — original page by Nathan Marz](http://lambda-architecture.net/)
- Compare: **Kappa** (stream-only) — easier ops but expensive replay/backfill.
- *Why we picked Lambda:* we needed historical reprocessing on real BDSP EDF
  files (a 17 GiB cold corpus) and a live alert path at the same time.

### 2.2 Medallion (bronze → silver → gold) (5 min)
- **Bronze** = raw landed events, immutable, append-only (JSONL in our repo).
- **Silver** = cleaned, deduped, conformed (Parquet, partitioned).
- **Gold** = business-ready aggregates (Parquet, partitioned by date).
- Read: [Databricks — medallion architecture](https://www.databricks.com/glossary/medallion-architecture)
- *Why Parquet for silver/gold:* columnar + Snappy → about **42× smaller**
  than our JSONL bronze for the same rows; predicate/column pushdown; splittable
  for parallel reads.

### 2.3 Apache Kafka (30 min — most important to internalize)
- **Mental model:** a distributed, partitioned, replayable commit log.
  Producers append; consumers read at their own offset; data is retained for a
  configurable window (so consumers can catch up or replay).
- KRaft mode = Kafka without ZooKeeper (post-3.5). We run **3.9 in KRaft**.
- Read:
  - [Kafka — Introduction](https://kafka.apache.org/intro)
  - [KRaft overview](https://kafka.apache.org/documentation/#kraft)
- Concepts you must own: **topic, partition, offset, consumer group, retention,
  watermarks (from the consumer side)**.

### 2.4 Apache Spark (45 min)
- **Mental model:** lazy DAG of transformations over partitioned data; Catalyst
  optimizer turns SQL/DataFrame plans into stages; **Structured Streaming** is
  a micro-batch engine that re-runs the plan on each trigger with a watermark
  to bound state.
- Read:
  - [Spark — Structured Streaming guide](https://spark.apache.org/docs/3.5.5/structured-streaming-programming-guide.html)
  - [Spark — Catalyst optimizer (paper, optional)](https://people.csail.mit.edu/matei/papers/2015/sigmod_spark_sql.pdf)
- Internalize: **watermarks, windowed aggregation, stateful joins, output modes
  (append/update/complete), checkpointing, broadcast vs sort-merge join, row_number
  window functions.** All show up in §6.

### 2.5 Apache Cassandra (15 min)
- **Mental model:** wide-column NoSQL, masterless, tunable consistency.
  **Partition key** decides which node holds the row; **clustering key**
  decides on-disk order *inside* the partition.
- Read: [Cassandra — data modeling 101](https://cassandra.apache.org/doc/4.1/cassandra/data_modeling/data_modeling_rdbms.html)
- Our schema (read this twice):
  ```sql
  CREATE TABLE brainwatch.alerts (
    patient_id  text,
    alert_time  timestamp,
    severity    text,
    anomaly_score float,
    explanation text,
    PRIMARY KEY (patient_id, alert_time)
  ) WITH CLUSTERING ORDER BY (alert_time DESC);
  ```
  - Partition key `patient_id` → all alerts for a patient land on the same node
    (cheap "show me this patient's alerts" query).
  - Clustering key `alert_time DESC` → newest-first read without an `ORDER BY`.

### 2.6 EEG / EDF (15 min)
- **EEG (electroencephalography):** brain electrical activity recorded as a
  multi-channel time-series (typically 19–50 channels, 200–512 Hz).
- **EDF (European Data Format):** the standard binary container — header
  (channel names, sampling rate, duration) + raw samples.
- Read: [EDF spec — Kemp et al., 1992](https://www.edfplus.info/specs/edf.html)
- We parse EDF with **MNE-Python** (`mne.io.read_raw_edf`). Code path:
  `scripts/edf_to_bronze.py`.

### 2.7 EHR / ICD-10 / HEEDB (10 min)
- **EHR (electronic health record):** clinical events — vitals, labs, meds,
  diagnoses.
- **ICD-10:** the international diagnosis coding system. We use the real
  **HEEDB neurology table** from BDSP (ICD-10 codes filtered to neuro).
- Code path: `src/brainwatch/analytics/heedb.py`.

### 2.8 Kubernetes basics (45 min)
- **Mental model:** desired-state controller. You give it manifests; the
  control loop converges reality to match.
- Objects you'll see in our manifests:
  - **Deployment** — stateless rolling pods (`kafka-producer`, `speed-layer`,
    `cassandra-exporter`).
  - **StatefulSet** — pods with stable identity + per-pod PVC (`kafka`,
    `cassandra`).
  - **PVC / PV** — persistent volume claim + the EBS volume that backs it.
  - **Job / CronJob** — run-to-completion (`cassandra-schema-init`,
    `spark-batch-cronjob`).
  - **Service / NodePort** — stable DNS + cluster-IP for pods.
- Read: [Kubernetes — concepts](https://kubernetes.io/docs/concepts/)

### 2.9 AWS EKS / S3 / EBS (20 min)
- **EKS:** Amazon's managed Kubernetes control plane. We create the cluster
  with **`eksctl`** and back PVCs with **EBS** via the AWS EBS CSI driver
  (`gp3` StorageClass).
- **S3:** object storage. We use it for **two roles** — the data lake bucket
  (`brainwatch-capstone-…`) and the **static-website dashboard bucket**
  (`brainwatch-dashboard-…`).
- **EBS snapshots:** point-in-time backups of EBS volumes; storage is billed
  on **used blocks**, not provisioned size — that's why our pause is cheap.

### 2.10 Grafana + Infinity datasource (10 min)
- **Grafana 11** for dashboards.
- **yesoreyeram-infinity-datasource:** lets Grafana read JSON from any URL.
  We point it at the static S3 dashboard bucket — that's how dashboards keep
  working with the EKS cluster torn down.

---

## 3. Problem domain

A neuro-ICU nurse cannot watch 50 simultaneous live EEG streams. The clinical
goal is to **auto-flag anomalies on the live signal and combine them with
clinical context (labs, meds, diagnoses)**, so a clinician can act in seconds.

Concretely, BrainWatch must:

1. **Ingest at scale** — ≥17 GiB of real EEG (we ship 17 GiB / 1,571
   recordings across 4 hospital sites) and the matching EHR.
2. **Process two ways at once:**
   - **Batch layer** — periodic accurate recompute (bronze → silver → gold).
   - **Speed layer** — sub-minute alerts on the live stream.
3. **Serve** — alerts queryable by patient; dashboards that the team and the
   examiner can open in a browser.
4. **Run on real infrastructure** — Kubernetes (AWS EKS).

This is exactly the **Lambda architecture** problem statement.

---

## 4. Architecture

The picture every team member must be able to draw on a whiteboard:

```
 REAL EDF (Harvard BDSP S3, credentialed access point)
        │  scripts/download_real_edf.py  (metadata-driven, breadth-first)
        ▼
 Local EDF files  ──►  scripts/edf_to_bronze.py
                              │   (mne parses EDF; per-window quality features)
                              ▼
                       BRONZE  data/lake/bronze_real/eeg/...jsonl
                              │
 Real HEEDB ICD-10  ──►  scripts/build_real_ehr.py
                              │
                       BRONZE  data/lake/bronze_real/ehr/...jsonl
 ─────────────────────────────────────────────────────────────────────
                              │
                   ┌──────────┴──────────┐
                   ▼                     ▼
              BATCH layer           SPEED layer
       (processing/silver_layer.py)  (processing/speed_layer.py)
          dedup + quality_flag       Kafka readStream →
          → SILVER (Parquet)          windowed agg +
          patient_features            anomaly score UDF →
          → GOLD   (Parquet)          foreachBatch →
                   │                  Cassandra
                   ▼                     │
            Alerts dataset               ▼
            build_alerts_dataset.py    alerts table  (PK patient_id,
                                                     CK alert_time DESC)
                                          │
                                          ▼
                              scripts/cassandra_to_s3_exporter.py
                                          │  (polls Cassandra, builds rollups,
                                          │   uploads JSON to S3 every 3s)
                                          ▼
                              S3 static-website bucket
                                          │
                                          ▼
                                Grafana 11 (Infinity datasource)
                                — 5 dashboards (Live Alerts, Pipeline,
                                  Insights, Explorer, About)
```

### Why each box is the way it is

| Box | Choice | Why |
|---|---|---|
| Bronze format | **JSONL** | Append-only, human-readable, line-splittable, no schema migration pain at the landing edge. |
| Silver/Gold format | **Parquet + Snappy** | Columnar, ~42× smaller, predicate/column pushdown, splittable. |
| **Distributed FS** | **HDFS (NameNode + 2 DataNodes, RF=2)** | Compute-side distributed storage. Bronze/silver/gold lake + Spark checkpoints. Literal "HDFS or equivalent" rubric match. |
| **Object store** | **S3 static-website bucket** | Serving-side. Rollup JSON for dashboards — survives cluster teardown for ~$1/mo. |
| Stream broker | **Kafka 3.9 KRaft** | No ZooKeeper. Replayable. Decouples producer rate from consumer rate. |
| Stream engine | **Spark Structured Streaming** | Same DataFrame API as batch → one team, one mental model. Watermarks bound state. |
| Hot store | **Cassandra 4.1** | Fast writes, partition by `patient_id` gives O(1) "this patient's alerts." |
| Cluster | **AWS EKS + EBS gp3** | Managed control plane; EBS CSI for stateful workloads; we know the cost model. |

---

## 5. Repo tour

```
Big-Data-Project/
├── CLAUDE.md                   ← entry-point for AI agents (real source of truth on “how to run”)
├── README.md                   ← human entry point
├── CODE_PLAN.md                ← sprint plan, what landed in which week
├── CONTRIBUTORS.md             ← role / module ownership per team member
├── pyproject.toml              ← deps, extras: [dev], [spark], [kafka]
├── configs/project.example.yaml← runtime config (Kafka topics, paths, watermarks)
│
├── src/brainwatch/             ← THE library
│   ├── contracts/events.py     ← EEGChunkEvent, EHREvent, AlertEvent dataclasses
│   ├── ingestion/              ← Kafka helpers, bronze writer, EDF / EHR loaders
│   ├── processing/             ← bronze_ingest, silver_layer, gold_layer, speed_layer
│   ├── analytics/              ← heedb (real ICD-10), icd_codes, rollups
│   ├── serving/                ← anomaly_rules (scoring), cassandra_sink, alert_publisher
│   └── config/settings.py      ← YAML loader
│
├── scripts/                    ← runnable entry points (no business logic here, just glue)
│   ├── download_real_edf.py    ← BDSP metadata-driven downloader
│   ├── edf_to_bronze.py        ← parse real EDF → JSONL bronze
│   ├── build_real_ehr.py       ← real-cohort EHR with HEEDB ICD-10
│   ├── run_batch.py            ← silver + gold + alerts
│   ├── run_speed_layer_kafka.py← speed layer driver (submitted by k8s pod)
│   ├── kafka_producer_driver.py← replays bronze JSONL into Kafka
│   ├── cassandra_to_s3_exporter.py ← Cassandra → S3 every 3 s
│   ├── export_layer_samples.py ← samples + counts per layer for the Explorer dashboard
│   ├── add_note.py             ← append a note to dashboard/public/notes.json + S3
│   └── …
│
├── infra/
│   ├── docker/docker-compose.yml          ← local Kafka + Spark for dev
│   ├── k8s/                               ← original local-K8s manifests
│   └── cloud/
│       ├── deploy_cloud.sh                ← EKS bring-up (idempotent)
│       ├── resume_from_snapshots.sh       ← restore everything from EBS snapshots
│       ├── grafana-*.json                 ← 5 dashboards (see §11)
│       └── k8s-overlays/real-pipeline.yaml← the real-data EKS overlay (read §9.2)
│
├── tests/                      ← 131 tests (pytest)
├── docs/                       ← read in the order given in §1
├── data/                       ← local data lake (gitignored)
└── artifacts/eks/snapshots/    ← snapshot inventory + resume coordinates
```

---

## 6. Code walk

Read the files in **this order** — it follows the data, not the directory tree.

### 6.1 Contracts — `src/brainwatch/contracts/events.py`
The three dataclasses every other module talks in: `EEGChunkEvent`, `EHREvent`,
`AlertEvent`. Look at the fields once, then move on; they're stable.

### 6.2 Download — `scripts/download_real_edf.py`
- Reads BDSP root-key creds from `credentials/rootkey.csv` (parsed with
  `encoding="utf-8-sig"` to strip the BOM — a real bug we hit).
- Reads each site's `eeg-metadata.csv`, filters by `DurationInSeconds`, then
  goes **round-robin across sites** so we get breadth (many subjects) instead
  of depth (a few long recordings).
- Lands EDF files under `data/lake/edf_raw/<site>/...`.

### 6.3 EDF → Bronze — `scripts/edf_to_bronze.py`
- `_quality()` opens each EDF with **MNE**, computes per-window:
  `signal_quality_score`, `mean_amplitude_uv`, `flat_channel_frac`,
  `clipping_frac`. **These are measured, not synthetic.**
- Emits one JSONL line per windowed chunk.

### 6.4 EHR — `scripts/build_real_ehr.py`
- Keys EHR events to the **real cohort** from §6.2 (same `patient_id`).
- Uses **real HEEDB ICD-10** codes via
  `src/brainwatch/analytics/heedb.py`.

### 6.5 Silver — `src/brainwatch/processing/silver_layer.py`
- `_read_bronze(spark, path)` **sniffs format** (JSONL vs Parquet) by
  walking the directory — handles both our JSONL bronze and any earlier
  Parquet bronze.
- `build_eeg_silver`:
  - `dropDuplicates(["patient_id", "session_id", "event_time"])` — true dedup.
  - Adds `quality_flag` ∈ {`OK`, `LOW_SR`, `SHORT_WINDOW`}.
  - Writes **partitioned by `site_id`, `ingestion_date`**.
- `build_ehr_silver` — uses `row_number()` over a window to keep only the
  **latest version** of each EHR event.
- `build_patient_dim` — a slowly-changing dimension keyed by **sha1** of the
  patient identifier.

### 6.6 Gold — `src/brainwatch/processing/gold_layer.py`
- `build_patient_features`:
  - Broadcasts the small `patient_dim` (`F.broadcast(patient_dim)`) so Spark
    does a **broadcast join** instead of a shuffle.
  - Joins EHR to EEG within a **±30-minute window**.
  - Rolls up per `patient_id × event_date`: `n_eeg_chunks`,
    `mean_sampling_rate`, `has_critical_lab_today`, `n_medication_changes`.
- `build_alert_summary` — summarizes the alerts dataset by severity.

### 6.7 Anomaly scoring — `src/brainwatch/serving/anomaly_rules.py`
- `compute_anomaly_score(features)` — weighted sum, clamped to [0, 1]:
  `0.30·chunk + 0.25·quality + 0.30·critical + 0.15·meds`. **These weights are
  load-bearing; do not casually drift them.**
- `classify_v2(score, has_critical_lab)`:
  - critical-lab fast path: `has_critical_lab and score ≥ 0.60 → critical`.
  - thresholds otherwise: 0.85 critical / 0.65 warning / 0.40 advisory.

### 6.8 Speed layer — `src/brainwatch/processing/speed_layer.py`
- `build_kafka_streaming_pipeline`:
  - `spark.readStream.format("kafka")` from `eeg.raw`.
  - **30-second event-time watermark**, **30-second window** with **15-second
    slide** — bounds state to about one window-width.
  - UDF wraps `classify_v2`; computes severity column.
  - `foreachBatch` writes to Cassandra using `cassandra-driver`.
  - **Output mode: append** — we abandoned the stream-stream EHR join because
    Spark forbids update-mode stream-stream join with our requirement.
- Determinism: severity keys use `zlib.crc32` instead of Python's `hash()`
  (which is salted per-process).

### 6.9 Serving — `src/brainwatch/serving/cassandra_sink.py`
- Wraps `cassandra-driver` cluster setup. **Always `try`/`finally`** the
  `Cluster` — leaking it across re-runs was a real bug we fixed.

### 6.10 Rollups — `src/brainwatch/analytics/rollups.py`
- The canonical dashboard rollups (`build_summary`, `build_severity_breakdown`,
  `build_timeline`, `build_score_histogram`, `build_top_patients`,
  `build_recent`). Refactored out of duplication; **the exporter pod
  installs the project wheel so this module is importable in-cluster.**

### 6.11 Exporter — `scripts/cassandra_to_s3_exporter.py`
- Polls Cassandra every 3 s (configurable), builds the rollups above, uploads
  to S3 with `CacheControl: no-cache,max-age=0` so Grafana sees fresh data.

### 6.12 Explorer — `scripts/export_layer_samples.py`
- Writes one JSON sample + a row-count per layer to
  `dashboard/public/explorer/`, which the **Data Explorer dashboard**
  (`infra/cloud/grafana-explorer-dashboard.json`) reads via Infinity.

### 6.13 Notes — `scripts/add_note.py`
- Append a timestamped observation:
  ```bash
  python scripts/add_note.py --layer silver --tag dedup \
      --text "dedup dropped 565 dup EEG windows" --author quang
  ```
  Lands in `dashboard/public/notes.json` and, with AWS creds present, syncs
  to S3 so the "Observations / Notes" Grafana panel updates live.

---

## 7. Running locally

The 30-minute happy path:

```bash
# 1. Environment
source /mnt/disk1/aiotlab/envs/uffm/bin/activate     # shared host venv
pip install -e ".[dev,spark,kafka]"
export BDSP_CREDENTIALS=/mnt/disk1/aiotlab/pqhung/courseworks/credentials/rootkey.csv

# 2. Bring up Kafka + Spark locally
docker compose -f infra/docker/docker-compose.yml up -d
# Kafka UI: http://localhost:8890   Spark UI: http://localhost:8891

# 3. Get data (small slice for local dev — full 17 GiB lives in S3 raw_edf/ (the streamer reads from there))
python scripts/download_real_edf.py --target-gib 1 --sites 4 --max-duration-s 600
python scripts/edf_to_bronze.py
python scripts/build_real_ehr.py

# 4. Batch (silver + gold + alerts)
python scripts/run_batch.py

# 5. Speed (one terminal each)
python scripts/kafka_producer_driver.py --bootstrap localhost:9092 --rate 50
python scripts/run_speed_layer_kafka.py

# 6. Export layer samples for the Data Explorer dashboard
python scripts/export_layer_samples.py

# 7. Inspect — open Grafana locally and load any infra/cloud/grafana-*.json
```

For the cassandra-less local path, `scripts/end_to_end_demo.py` orchestrates
replay → bronze → silver → gold → alert validation in one pass and is the
fastest way to *see* data flowing.

---

## 8. Running on K8s — local

```bash
bash infra/k8s/deploy.sh                  # default namespace `brainwatch`
NAMESPACE=foo bash infra/k8s/deploy.sh    # override
bash infra/k8s/deploy.sh --dry-run        # render only
bash infra/k8s/teardown.sh                # remove everything
```

`deploy.sh` applies in dependency order:
**namespace → configmap → PVCs → Cassandra StatefulSet → Spark streaming
Deployment → Spark batch CronJob.**

The local manifests use `local-path` provisioning, so this only needs `kind`
or `minikube` — no AWS.

---

## 9. Running on EKS — cloud, real data

This is the path your demo runs on.

### 9.1 One-shot bring-up (cold start)

```bash
export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... AWS_DEFAULT_REGION=us-east-1
bash infra/cloud/deploy_cloud.sh
```

What it does, in order:
1. `eksctl create cluster brainwatch` (managed control plane + 2 m5.xlarge
   nodes).
2. Installs the **EBS CSI driver** and a **`gp3` StorageClass**.
3. Creates the `brainwatch` namespace and the `aws-credentials` Secret.
4. Applies `infra/cloud/k8s-overlays/kafka-kraft.yaml` (Kafka 3.9 KRaft
   StatefulSet — `apache/kafka:3.9.0`, `fsGroup: 1000`, busybox initContainer
   to clear `lost+found`).
5. **`infra/cloud/deploy_hdfs.sh`** applies the hybrid storage layer:
   `hdfs.yaml` (1 NameNode + 2 DataNodes StatefulSets) +
   `batch-on-hdfs.yaml` (**two CronJobs — bronze loader every 5 min, Spark
   batch every 5 min offset by 2 min**). See §11 below.
6. Applies `infra/cloud/k8s-overlays/real-pipeline.yaml` — the **four pieces
   that make this real-data**:

| Object | Image | What it does |
|---|---|---|
| `cassandra-schema-init` Job | `cassandra:4.1` | Waits for Cassandra, creates the `alerts` and `patient_state` tables. |
| `kafka-producer` Deploy | `python:3.11-slim` | Init-container pulls the project wheel from S3; main container replays bronze JSONL into Kafka at 150 events/s. |
| `speed-layer` Deploy | `spark:3.5.5-scala2.12-java17-python3-ubuntu` | `pip install --target=/code/site-packages` (the image's `/home/spark` is read-only), `PYTHONPATH=/code/site-packages`, then `spark-submit --packages spark-sql-kafka` running `scripts/run_speed_layer_kafka.py`. |
| `cassandra-exporter` Deploy | `python:3.11-slim` | Installs the project wheel + `cassandra-driver` + `boto3`, runs `cassandra_to_s3_exporter.py` every 3 s. |

7. Applies the Cassandra StatefulSet and Grafana Deployment (with NodePort
   Service so the dashboard is publicly reachable on the worker EIP).
8. Uploads the static dashboard payload (`dashboard/public/**`) to
   `s3://brainwatch-dashboard-…` — that bucket is configured as a static
   website, so it stays up even with the cluster torn down.

### 9.1a The hybrid storage layer (HDFS + S3)

The compute side of the pipeline runs against **HDFS**:

| Storage | Layer | Path | Purpose |
|---|---|---|---|
| HDFS | bronze | `hdfs://hdfs-namenode:8020/lake/bronze` | Spark batch input |
| HDFS | silver | `hdfs://hdfs-namenode:8020/lake/silver` | Spark batch output |
| HDFS | gold | `hdfs://hdfs-namenode:8020/lake/gold` | Spark batch output |
| HDFS | checkpoints | `hdfs://hdfs-namenode:8020/checkpoints` | Speed-layer state |
| S3 | **raw EDF archive** | `s3://brainwatch-capstone-…/raw_edf/` (17 GiB / 1,571 EDFs) | Source for `bronze-streamer` |
| S3 | dashboard rollups | `s3://brainwatch-dashboard-…/*.json` | Grafana Infinity reads |
| S3 | **cluster state** | `s3://brainwatch-dashboard-…/cluster_*.json` | Architecture Status dashboard |
| S3 | data explorer | `s3://brainwatch-dashboard-…/explorer/*.json` | Layer samples |
| S3 | project code | `s3://brainwatch-capstone-…/code/*.whl` | Init-container fetches |
| Cassandra | live alerts | `brainwatch.alerts` table | Speed-layer foreachBatch sink |
| EBS PVC | Kafka log, Cassandra SSTables | per-pod | StatefulSet storage |

**Dynamic bronze production:** the `bronze-streamer` Deployment reads EDFs
from S3 and writes JSONL features to bronze-pvc **and copies the raw EDF
binary into `bronze/edf/`** (archive pattern, capped at 4 GiB to fit HDFS).
The two batch CronJobs (every 5 min) sync to HDFS and rebuild silver/gold
— so each successive batch sees more bronze than the last.

**Kafka real-stream loop:** `kafka-producer` Deployment replays bronze
JSONL into `eeg.raw` + `ehr.updates` topics at ~333 events/s. Speed-layer
Spark consumes from Kafka with watermarks, scores via the UDF, writes alerts
to Cassandra via `foreachBatch`. Both topics carry **1 M+ events** and grow
continuously — verifiable via
`kubectl -n brainwatch exec kafka-0 -- /opt/kafka/bin/kafka-get-offsets.sh
 --bootstrap-server localhost:9092 --topic eeg.raw`.

**Pipeline dashboard layout — 6 stats × 2 rows:** raw/bronze/silver/gold/events
(row 1) + streamed/alerts/compression/batch/gen/tests (row 2), plus a live
**Data-lake zone sizes** table-with-gauge-cells panel and the historical
**Pipeline stage timings** snapshot.

The `LAKE_BASE` env var on the speed-layer Deployment + the spark-batch Job
flips the lake between local file paths and `hdfs://…`. Locally for tests
it defaults to `data/lake`. See `QA-BANK.md` §17 for the full Q&A.

### 9.2 The non-obvious deploy bugs (and how we fixed them)

These are all in `real-pipeline.yaml` if you want to look at the fix:

| Symptom | Root cause | Fix |
|---|---|---|
| `CANNOT_READ_FILE_FOOTER` reading bronze | JSONL was being read as Parquet | `_read_bronze` walks the dir and sniffs format. |
| OOM at 8 GiB batch | default 1 GiB driver | `--driver-memory 24g`, `spark.sql.shuffle.partitions=256`, adaptive on. |
| Python 3.8 too old in Spark image | apache/spark:3.5.4 default | Switched to `spark:3.5.5-scala2.12-java17-python3-ubuntu` (Python 3.10). Relaxed `requires-python` to `>=3.10`. |
| `pip install --user` errored | `/home/spark` is read-only | `pip --target=/code/site-packages` + `PYTHONPATH`. |
| Kafka pod crash on first boot | `lost+found` on EBS volume + `fsGroup` mismatch | busybox initContainer removes `lost+found`; pod `securityContext.fsGroup: 1000`. |
| `stream-stream join not supported in Update mode` | We had output mode `update` | Switched to `append` and dropped the EHR-join from the live demo (EEG-only windowing). |
| Restart `2 sources vs 1` mismatch | stale checkpoint dir | `rm -rf /data/checkpoints/kafka_speed_layer` on restart (already inlined in the manifest). |

### 9.3 Verifying the cloud run

After bring-up wait ~3 minutes, then:

```bash
kubectl -n brainwatch get pods
# All Running. The 4 deployments + Cassandra + Kafka + Grafana.

kubectl -n brainwatch exec -it cassandra-0 -- cqlsh -e \
  "SELECT COUNT(*) FROM brainwatch.alerts;"
# Should grow each time you re-run.

curl -s http://brainwatch-dashboard-923884399064.s3-website-us-east-1.amazonaws.com/rollups/summary.json | jq
# Live numbers — total alerts, severity breakdown.
```

The Grafana NodePort URL is printed by `deploy_cloud.sh` at the end.

---

## 10. Tests

131 tests, all green at the last full run. Layout:

```
tests/test_anomaly_rules.py        ← v1 + v2 classification
tests/test_anomaly_boundaries.py   ← exact threshold values (0.40 / 0.65 / 0.85)
tests/test_bronze_writer.py        ← sha256 dedup + dead-letter routing
tests/test_silver_layer.py         ← dedup + partitioning (Spark)
tests/test_gold_layer.py           ← broadcast join + rollups (Spark)
tests/test_speed_layer.py          ← UDF + windowed aggregation (Spark)
tests/test_heedb.py                ← ICD-10 lookups, HIGH_ACUITY frozenset
tests/test_dashboard_rollups.py    ← the rollups module
tests/test_dead_letter.py          ← daily JSONL routing
tests/test_edf_quality.py          ← measured signal-quality features
…
```

Running:

```bash
pytest -v                                              # all
pytest tests/test_speed_layer.py -v                    # one file
pytest tests/test_speed_layer.py::test_window_dedup -v # one test
pytest -m "not spark"                                  # skip Spark-dependent tests
```

Spark-dependent tests guard themselves with `@pytest.mark.skipif(...)` based
on whether `pyspark` imports — they silently skip if the `spark` extra isn't
installed. There is **no `conftest.py`** (`pyproject.toml` sets `pythonpath`
and `testpaths`).

---

## 11. Demo runbook

The script for the live defense — 7 minutes from "share screen" to "thank you."

| Minute | Show | Say |
|---|---|---|
| 0:00 | The architecture picture (§4) | "BrainWatch is a Lambda-architecture platform — batch + speed — over 17 GiB of real Harvard BDSP EEG (dynamic — bronze grows during the demo)." |
| 0:30 | `kubectl -n brainwatch get pods` (or screenshot) | "Live on AWS EKS. Kafka, Spark Structured Streaming, Cassandra, Grafana." |
| 1:00 | Grafana **Live Alerts** dashboard | Point at severity counts ticking up in real time. |
| 2:00 | Grafana **Pipeline** dashboard | "spark-batch-hdfs CronJob fires every 5 min and rebuilds silver+gold in ~50 s; ~42× compression on the JSONL → Parquet path." |
| 3:00 | Grafana **Insights** dashboard | "These are real HEEDB ICD-10 categories — most prevalent: epilepsy, encephalopathy." |
| 4:00 | Grafana **Data Explorer** dashboard | "Bronze raw → Silver dedup+quality → Gold daily features. Same row, three zones." |
| 5:00 | `scripts/add_note.py` in a terminal | "We can annotate live findings — they appear in the Notes panel." |
| 6:00 | Costs (§13) | "Compute torn down between demos, ~$1/mo storage. Resume in 15 min." |
| 6:30 | Q&A | (See §12.) |

If anything goes red, fall back to the **static S3 dashboard URL** — it
serves the last snapshot and is independent of the EKS cluster.

---

## 12. Defense Q&A

Already in `docs/PRESENTATION-GUIDE.md`. The 10 you must rehearse:

1. **Why Lambda and not Kappa?** Reprocessing 17 GiB cold data is cheap with
   a batch layer; in Kappa we'd re-stream it from Kafka, which means we'd need
   to keep that retention. The batch layer also gives us a *trustable* source
   for back-testing the speed layer's UDF.
2. **Why Kafka in KRaft mode?** No ZooKeeper → one fewer stateful service to
   operate. Apache deprecated ZK in 3.5+.
3. **Why Parquet?** Columnar + Snappy ≈ 42× smaller than our JSONL bronze for
   the same rows; predicate pushdown; splittable for Spark.
4. **Why Cassandra and not Postgres for alerts?** Write-heavy, append-mostly,
   trivially partitionable by `patient_id`, masterless → linear write scaling.
5. **How does the watermark bound state?** Watermark = max event-time seen
   minus the allowed lateness (30 s here). State for windows older than the
   watermark is evicted; new late events past it are dropped.
6. **What's in the anomaly score?** `0.30·chunk + 0.25·quality + 0.30·critical
   + 0.15·meds`, clamped to [0, 1]. Critical-lab fast path at 0.60.
7. **Why broadcast join in gold?** `patient_dim` is small; broadcasting it
   avoids a shuffle — orders of magnitude faster.
8. **What does the dashboard depend on?** Only S3. Grafana reads JSON via the
   Infinity datasource; the EKS cluster can be torn down and the dashboard
   still serves.
9. **What about CAP?** Cassandra is AP (with tunable consistency). For alerts
   we use `LOCAL_ONE` on writes — we tolerate transient under-replication for
   throughput; reconciliation is via the batch layer.
10. **What did you learn?** See `docs/final-report.md` §"Lessons Learned" —
    the 11-category answer that aligns to the rubric.

---

## 13. Cost, teardown, resume

| Mode | Hourly | Monthly | What's running |
|---|---|---|---|
| Demo / development on EKS | ~$0.40/h | — | 1 control plane + 2 m5.xlarge + 5 EBS volumes + 1 NAT gateway |
| Paused (current) | ~$0.00/h | **~$1** | 5 EBS snapshots (~60 GiB provisioned, billed on used blocks) + 2 S3 buckets |

### Teardown to "paused"

`bash infra/cloud/teardown_and_snapshot.sh` (or the steps in
`artifacts/eks/snapshots/index.txt`):

1. Scale all deployments / statefulsets to 0.
2. `aws ec2 create-snapshot` on each PVC's underlying EBS volume.
3. `eksctl delete cluster brainwatch` — control plane, nodes, NAT, all EBS
   volumes go away.
4. Snapshots remain, dashboard S3 keeps serving.

### Resume

```bash
bash infra/cloud/resume_from_snapshots.sh   # ~15–20 minutes
```

This script:

1. `eksctl create cluster brainwatch`.
2. Creates new EBS volumes **from each snapshot** (looked up in
   `artifacts/eks/snapshots/index.txt`).
3. Statically provisions a PV for each restored volume.
4. Re-applies the manifests; PVCs bind to the restored PVs by name → pods
   come up with data intact.
5. Re-uploads the project wheel + scripts to the code bucket (so the
   init-containers in `real-pipeline.yaml` can fetch them).

---

## 14. Glossary

| Term | Meaning |
|---|---|
| **Bronze / Silver / Gold** | Medallion data-lake zones: raw / cleaned / business-ready. |
| **BDSP** | Brain Data Science Platform (Harvard/MGH) — credentialed access EEG corpus. |
| **CSI driver** | Container Storage Interface — pluggable storage back-end for K8s (we use EBS CSI). |
| **EBS gp3** | AWS general-purpose SSD volume; baseline 3000 IOPS. |
| **EDF** | European Data Format — standard binary EEG container. |
| **EHR** | Electronic Health Record. |
| **EKS** | AWS-managed Kubernetes control plane. |
| **`eksctl`** | The Amazon CLI for declarative EKS create/delete. |
| **Foreach batch** | Structured Streaming sink that hands you a DataFrame per micro-batch — we use it to write to Cassandra. |
| **HEEDB** | The BDSP neurology ICD-10 catalogue we use. |
| **ICD-10** | International Classification of Diseases v10 — diagnosis codes. |
| **Infinity datasource** | Grafana plugin (`yesoreyeram-infinity-datasource`) that reads JSON from a URL. |
| **JSONL** | One JSON object per line — append-friendly bronze format. |
| **KRaft** | Kafka Raft — Kafka's own consensus, replaces ZooKeeper. |
| **Lambda architecture** | Two parallel paths over the same data: batch + speed. |
| **MNE-Python** | The EDF/EEG parsing library we use in `edf_to_bronze.py`. |
| **NodePort** | K8s Service type exposing a port on every node — our dashboard ingress. |
| **PVC / PV** | Persistent Volume Claim / Persistent Volume — request / actual storage. |
| **StatefulSet** | K8s controller giving each pod a stable name + per-pod PVC. |
| **Watermark** | Event-time threshold in Structured Streaming; state older than this can be evicted. |

---

## 15. Cross-doc map

When you need… | Read…
---|---
The mental model + reading order (this) | `docs/STUDY-GUIDE.md`
A single printable page to take on stage | `docs/CHEATSHEET.md`
The full question bank, every Q answered | `docs/QA-BANK.md`
A high-level architecture picture | `docs/architecture.md`
A deeper why-this-tech write-up | `docs/TECHNOLOGY.md`
A defense cheat-sheet of likely Q's | `docs/PRESENTATION-GUIDE.md`
The full course-rubric write-up | `docs/final-report.md`
Slides | `docs/final-slides.md`
Local dev environment setup | `docs/setup-guide.md`
What landed in which week | `docs/week1-*.md`, `docs/week2-*.md`, `CODE_PLAN.md`
Module ownership per team member | `CONTRIBUTORS.md`
Snapshot IDs to resume from | `artifacts/eks/snapshots/index.txt`
The pause/resume cookbook | `infra/cloud/resume_from_snapshots.sh`
The EKS overlay (single source of truth) | `infra/cloud/k8s-overlays/real-pipeline.yaml`

---

**You are ready.** If you only remember three things from this document:

1. **Lambda = batch + speed over the same data**, merged at serving — that's
   the rubric, that's our architecture.
2. **Bronze JSONL → Silver Parquet → Gold Parquet**, with a Kafka/Spark/Cassandra
   speed path running in parallel, all on EKS.
3. **Costs go to zero** when the cluster is deleted because the **dashboard
   lives on S3** and the **data lives on EBS snapshots** — resume in 15 minutes.

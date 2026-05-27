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
16. [Lessons learned, in plain English](#16-lessons-learned-in-plain-english)

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

## 16. Lessons learned, in plain English

The eleven lessons in the report (`Reflections.tex` in the Overleaf
project) all use the rubric's four-part structure (Problem Description
→ Approaches Tried → Final Solution → Key Takeaways). The version
below is the same content in plain language, so a new reader can
understand each lesson without opening the report. Lessons will be
added one at a time.

### Lesson 1 — Data Ingestion

**Problem (what hurt us).**
The BDSP catalogue gives us EEG recordings as one CSV per hospital
site. The CSVs are *not* schema-aligned across the five sites: one
site uses `DurationInSeconds`, another uses `RecordingDuration`; one
uses `SiteID`, another uses `InstituteID`; the service column has a
per-site vocabulary (S0001's `LTM` maps to a different BIDS task code
than S0002's `LTM`).

When we measured the full catalogue (306,741 rows) we found:

- **11,579 rows (3.8%)** with the duration field empty,
- **427 rows** below the 30-second hook-up threshold (too short to keep),
- **143,369 rows** with the service literally written `UNSPECIFIED`.

Three concrete bugs followed from this:

1. The per-site download script silently skipped every row whose
   column name did not match the one it hard-coded — so the
   downloader's success count was wrong without us noticing.
2. The manifest builder emitted broken S3 keys whenever the row's
   BIDS folder identifier was missing.
3. The streaming consumer crashed on the *first* malformed JSONL
   line because it had no quarantine path — one bad record killed
   the whole streaming query.

The dangerous part is *silent dropping*: a pipeline that throws away
4% of its inputs without recording the reason looks identical to a
pipeline with a small bug. Every downstream count became hard to
trust — silver dedup counts shifted between reruns because the
upstream had silently dropped different rows.

**What we tried.**

- *Approach 1 — one branch per site.* The first version of
  `src/brainwatch/ingestion/eeg_inventory.py` carried `if site ==
  "S0001": ...; elif site == "S0002": ...`. Trade-off: every new site
  adds another reviewed branch, and the alias differences sit inside
  those branches as magic strings. Broke down beyond three sites.
- *Approach 2 — fallback chain at the parsing boundary.* Use
  `row.get("DurationInSeconds") or row.get("RecordingDuration") or ""`.
  Trade-off: tolerates new aliases with zero code change, but a typo
  in a new alias silently returns the empty string. Mitigated by a
  unit test that asserts the parsed row has at least one non-empty
  field.
- *Approach 3 — strict `pydantic` schema with per-field validators.*
  Rejected. The canonical BDSP schema itself drifts across
  refreshes, so a strict schema would convert a recoverable drift
  into a hard pipeline failure.

**What we shipped.**

The fallback chain lives in
`src/brainwatch/ingestion/eeg_inventory.py` inside `parse_duration()`
and `build_candidate_s3_keys()`. Rows with a missing duration are
flagged into the manifest's quality counters and *excluded* from
`select_subset()`, so the downloader never even attempts a key whose
duration is unknown. The bronze writer
(`src/brainwatch/ingestion/bronze_writer.py`) routes every validation
failure to a daily dead-letter JSONL file under
`data/lake/_dead_letter/` with an explicit `reason` field. The
service-to-task mapping lives in a single `SERVICE_TASK_MAP`
dictionary; an unknown service falls back to `["EEG"]`, the BIDS
default.

Measured outcome: on the demonstration cohort the dead-letter queue
absorbed 17 malformed events across a thirty-minute run; none of them
stopped the pipeline, and the silver dedup count became stable across
reruns. The unit test
`tests/test_eeg_inventory.py::test_parse_duration_missing` pins the
fallback path against synthetic hostile inputs.

**Takeaway in one sentence.**
*Quarantine, do not discard.* Every validation / dedup / quality gate
in BrainWatch writes the rejected record to disk with a `reason`
field, so the next incident is investigable from the data itself,
with no reliance on cluster log retention.

**Where to look in the repo.**

- Parser with fallback chain → `src/brainwatch/ingestion/eeg_inventory.py`
  (`parse_duration`, `build_candidate_s3_keys`, `SERVICE_TASK_MAP`)
- Dead-letter queue → `src/brainwatch/ingestion/dead_letter.py` +
  `src/brainwatch/ingestion/bronze_writer.py`
- Tests pinning the failure modes → `tests/test_eeg_inventory.py`,
  `tests/test_bronze_writer.py::test_invalid_event_routed_to_dlq`,
  `tests/test_dead_letter.py`

**Beyond the EDF parser: the Kafka ingest boundary.**

Ingestion isn't just the EDF-side parsing; it's also the Kafka
publish path that hands events to the speed layer. Two design
choices land there:

1. **Producer configuration** (`scripts/kafka_producer_driver.py`):
   - `acks="all"` — the broker leader waits for every in-sync
     replica before acknowledging. The single-broker capstone has
     ISR = {0} so this is effectively `acks=1`; in the
     three-broker production posture it's the durability guarantee
     for the EEG stream.
   - `linger.ms=20` — wait 20 ms before sending so a few events
     batch into one TCP write. The default is 5 ms; we keep 20 ms
     because the per-event payload is ≈ 330 bytes and the broker's
     default `batch.size=16 KB` then holds ~50 messages, which is a
     better network-utilisation ratio than 5 ms's ~12.
   - `compression.type="gzip"` — JSON payloads compress to roughly
     a third, and gzip is broadly supported across consumers.
   - `retries=5` + `max_in_flight_requests_per_connection=1` — the
     producer waits for an ack between sends, so retries don't
     reorder messages within a partition.

2. **FileProducer fallback** (`src/brainwatch/ingestion/kafka_helpers.py`):
   `get_producer()` returns a `FileProducer` (writes JSONL to disk)
   when `kafka-python` isn't installed or the broker is
   unreachable. The local-dev story (no Kafka container, just
   write to disk) then mirrors the production story (Kafka topic)
   one-to-one — every script that uses `get_producer` works on a
   developer laptop without bringing up Docker.

**Kafka topic shape (also part of ingestion).**

| Topic | Partitions | Producer | Consumer |
|---|---|---|---|
| `eeg.raw` | 4 | `kafka_producer_driver.py` | speed-layer lookup + join queries |
| `ehr.updates` | 4 | EHR loader scripts | speed-layer join query |
| `alerts.anomaly` | 2 | `serving/alert_publisher.py` | downstream subscribers |
| `dead.letter` | 1 | `ingestion/dead_letter.py` | (audit only) |

Four partitions per stream topic lets the speed-layer Spark query
read with up to 4-way parallelism without rebalance. The single
partition on `dead.letter` is by design — DLQ records are read by
operators, not by streaming consumers, so partition parallelism
brings no benefit.

**Where to look in the repo (Kafka ingest path).**

- Producer driver with the config above →
  `scripts/kafka_producer_driver.py`
- Producer wrapper + FileProducer fallback →
  `src/brainwatch/ingestion/kafka_helpers.py`
- Topic creation (local dev) → `infra/docker/docker-compose.yml`
  (the `kafka-init` service)
- Tests pinning the round-trip + fallback →
  `tests/test_kafka_helpers.py` (3 tests)

---

### Lesson 2 — Data Processing with Spark

**Problem (what hurt us).**
The batch driver in `scripts/run_batch.py` chains four Spark
functions: `build_eeg_silver` (dedup on `(patient_id, session_id,
event_time)` + quality flag), `build_ehr_silver` (keep latest version
via a `row_number()` window), `build_patient_dim` (the small
patient-keyed dim), and `build_patient_features` (the gold join:
silver EEG ⋈ silver EHR within ±30 min, then ⋈ broadcast `patient_dim`).
An early local run on the 8.2 GiB cohort died after ~30 minutes with
`OutOfMemoryError` on the driver during the gold join.
`df.explain(extended=True)` showed the patient-dim join executing as
`SortMergeJoin` even though the dim is only ~2 MiB — an upstream
`select` projection had obscured the dim's true size from the
Catalyst optimiser, and the default `10 MiB`
`spark.sql.autoBroadcastJoinThreshold` did not fire.

**What we tried.**

- *Approach 1 — raise driver memory.* Bump `spark.driver.memory`
  from 1 → 4 → 24 GiB. Each step delayed the OOM but the join still
  spilled. A bigger heap masks skew until the next 2× cohort growth,
  then masks nothing.
- *Approach 2 — raise `spark.sql.autoBroadcastJoinThreshold`.*
  10 MiB → 50 MiB. Symptom disappears, but the join strategy is at
  the mercy of any upstream projection that obscures the size
  estimate.
- *Approach 3 — explicit `F.broadcast()` hint at the call site.*
  One extra symbol; the optimiser can't revert under adaptive query
  execution; the test that asserts the plan respects the hint
  becomes cheap.

**What we shipped.**
The hint sits on the join call in
`src/brainwatch/processing/gold_layer.py`. We set
`spark.sql.shuffle.partitions=256` for the 8.2 GiB local run and `16`
for the in-cluster CronJob; `spark.sql.adaptive.enabled=true` stays
on. The regression test
`tests/test_gold_layer.py::test_patient_dim_join_uses_broadcast` reads
`df.queryExecution.executedPlan` and fails if the plan reverts to
`SortMergeJoin`. Measured outcome: local batch completes in **47.8 s
on 8.2 GiB** (16-core `local[*]`, 24 GiB driver heap, 256 shuffle
partitions); in-cluster CronJob completes in ~50 s per fire on the
streamer-grown bronze.

**Takeaway in one sentence.**
*The shortest path to debugging a Spark job that surprises is
`df.explain(extended=True)`, not a heap-size bump.* Pin the broadcast
hint and write a plan-inspection test, because output-correctness
tests are blind to a join that quietly switches strategy.

**Where to look in the repo.**

- Gold-layer join with broadcast hint →
  `src/brainwatch/processing/gold_layer.py::build_patient_features`
- Batch driver chaining the four functions → `scripts/run_batch.py`
- Plan-inspection regression test →
  `tests/test_gold_layer.py::test_patient_dim_join_uses_broadcast`
- Spark config tuning →
  `infra/cloud/k8s-overlays/batch-on-hdfs.yaml`
  (`spark.sql.shuffle.partitions=16`, AQE on)

---

### Lesson 3 — Stream Processing

**Problem (what hurt us).**
The canonical Lambda speed-layer design was a stream-stream join of
`eeg.raw` ⋈ `ehr.updates` on `patient_id` within a tens-of-minutes
event-time predicate, with windowed aggregation downstream. Two Spark
Structured Streaming constraints combined badly: stream-stream joins
require `outputMode("append")`, and an append-mode join emits only
once the watermark passes the lower bound of the join window. With
our 30-second watermark and 30-second sliding window, end-to-end alert
latency exceeded one minute. Worse, the query failed to start in
`outputMode("update")` with the join present — Spark forbids that
combination (`AnalysisException: stream-stream join is not supported
in Update output mode`). For a clinician, an alert that arrives a
minute after the underlying signal can't be acted on for that window.

**What we tried.**

- *Approach 1 — `update` mode with the join.* Rejected at start-up
  by Spark.
- *Approach 2 — widen the watermark to 10 minutes.* The join
  matures, but the downstream aggregation waits 10 minutes too. A
  10-minute alert isn't an alert.
- *Approach 3 — continuous trigger.* Sub-second latency, but
  continuous trigger supports only `map`, `filter`, `select`.
  Neither the windowed aggregation nor the join fits.
- *Approach 4 — Lambda serving-layer lookup.* Batch path writes a
  per-patient EHR dimension into `brainwatch.patient_state`; speed
  layer's `foreachBatch` reads it in a single CQL
  `SELECT ... WHERE patient_id IN (...)` per micro-batch and scores
  the full v2 four-term formula. Each row is a partition-key seek
  on Cassandra, so the cost is dominated by network RTT. p50 alert
  visibility ≈ 12 s.
- *Approach 5 — Kafka stream-stream join (canonical).* Both
  `eeg.raw` and `ehr.updates` are streamed from Kafka, joined on
  `patient_id` within a ±30-min event-time predicate, windowed and
  scored. The inherent latency is ~60 s from append-mode +
  downstream windowed-agg emission. Useful as an accuracy benchmark
  against Approach 4.

**What we shipped.**
**Both Approach 4 and Approach 5 run concurrently** in the same
SparkSession through the `--mode=both` flag of
`speed_layer.main()`. The two queries write to `brainwatch.alerts`
tagged with a `source` column — `speed_lookup` (Approach 4) and
`speed_join` (Approach 5) — and the dashboard's Real-Time Alerts
panel filters on `source` to show the two paths side by side.
`spark.streams.awaitAnyTermination()` surfaces a crash in either
query without taking the other down silently.

The lookup query (`build_kafka_streaming_pipeline`) subscribes to
`eeg.raw` with `maxOffsetsPerTrigger=5000`, applies
`withWatermark("event_time", "30 seconds")`, aggregates per
`(patient_id, window(event_time, "30 seconds", "15 seconds"))`, and
in `foreachBatch` calls `fetch_patient_enrichment` to look up
`(has_critical_lab, n_medication_changes_24h)` for the batch's
distinct patients, then computes the score via
`anomaly_rules.compute_anomaly_score`. Trigger
`processingTime="5 seconds"`.

The join query (`build_kafka_join_pipeline`) streams both topics
from Kafka, applies `withWatermark` (30 s on EEG, 30 min on EHR),
left-outer joins on `patient_id` within a ±30-min event-time
predicate, groups per `(patient_id, window("1 minute", "30 seconds"))`,
extracts `has_critical_lab = max(when(event_type='critical_lab', 1))`
and `n_medication_changes_24h = sum(when(event_type='medication', 1))`
off the joined row, and scores with the same
`compute_anomaly_score`. Trigger `processingTime="30 seconds"`
(matches the append-mode emission cadence so the query isn't busy
between watermark advances).

Measured outcome (lookup path): 60–100 alerts per micro-batch at a
sustained 333 events/s per topic; cumulative reaches 100,000 alerts
at cohort scale; end-to-end p50 alert visibility ≈ 12 s.

**Takeaway in one sentence.**
*Exactly-once visibility is three properties together — a replayable
source (Kafka offsets in the Spark checkpoint), an idempotent sink
(Cassandra PK `(patient_id, alert_time)` makes a replayed insert a
no-op upsert), and checkpointed state (the Spark state store on
`checkpoints-pvc`).* When a streaming join is a latency hazard for
the live demo, the Lambda answer isn't to abandon the EHR features
— it's to read them from the batch-maintained serving-layer
dimension, while keeping the canonical stream-stream join live
alongside as the accuracy benchmark.

**Where to look in the repo.**

- Lookup pipeline (deployed default) →
  `src/brainwatch/processing/speed_layer.py::build_kafka_streaming_pipeline`
- Kafka stream-stream join pipeline →
  `src/brainwatch/processing/speed_layer.py::build_kafka_join_pipeline`
- CLI runner that starts both concurrently →
  `src/brainwatch/processing/speed_layer.py::main` (`--mode=both`)
- Batch-side dimension upsert →
  `src/brainwatch/processing/gold_layer.py::materialize_patient_enrichment`
- Per-batch lookup helper →
  `src/brainwatch/serving/cassandra_sink.py::fetch_patient_enrichment`
- Cassandra alerts schema with `source` column →
  `src/brainwatch/serving/cassandra_sink.py::init_keyspace`
- Empirical pod-delete check → `scripts/verify_exactly_once.sh`
- 7 unit tests for the enrichment path →
  `tests/test_serving_enrichment.py`

---

**What we added next (deeper batch insights).**

The bronze-streamer originally produced only summary features per
window (chunk count, mean sampling rate, quality score). To compute
clinically-meaningful insights on the batch side we added
`src/brainwatch/processing/eeg_features.py` — a pure-NumPy windowed
feature extractor that turns a `(n_channels, n_samples)` array plus
a sampling rate into 15 float features per window:

- **Band powers** (delta 0.5–4 Hz, theta 4–8, alpha 8–13, beta 13–30,
  gamma 30–45) absolute and relative.
- **Hjorth parameters** — activity, mobility, complexity (the
  classical seizure/state indicators from Hjorth 1970).
- **Line-length** — sum of |Δx|; a robust early-warning feature for
  ictal / interictal-continuum activity.
- **Spectral entropy** — Shannon entropy of the normalised PSD,
  bounded by log2(n_freqs); low for pure tones, high for noise.

Local-first workflow: `scripts/extract_eeg_features.py` uses MNE
(`mne.io.read_raw_edf`) to read an EDF on the developer station,
slides a window across each channel, runs
`extract_window_features` per window, and emits JSONL. The same
function is reused inside a Pandas UDF on the gold/batch path so
the Spark CronJob recomputes the rich feature row every five
minutes. Sixteen unit tests (`tests/test_eeg_features.py`) pin the
math against synthetic signals: pure 10 Hz sine → alpha-band
dominance, white noise → spectral entropy near maximum, flat
signal → zero line-length and zero Hjorth.

**Where to look in the repo (deeper-features path):**

- Feature math (pure NumPy) →
  `src/brainwatch/processing/eeg_features.py`
- Local extractor (MNE + JSONL out) →
  `scripts/extract_eeg_features.py`
- 16 math tests → `tests/test_eeg_features.py`

---

### Lesson 4 — Data Storage

**Problem (what hurt us).**
The rubric demands "HDFS or equivalent." Hadoop's S3A connector is a
first-class `FileSystem` implementation; modern Lakehouse deployments
sit on object storage. Three constraints pulled in different
directions: the paused-cost budget is under \$1/month (rules out
keeping EBS PVCs alive), the dashboard must stay reachable while the
cluster is torn down (rules out HDFS-only), and the deployed topology
must carry the literal word "HDFS" (discourages S3-only). The wrong
call in either direction would either 10× the monthly storage cost or
remove a required rubric component from the topology.

**What we tried.**

- *Approach 1 — S3 only.* Simplest operationally; eleven nines of
  durability from S3 itself. But "HDFS" appears nowhere in the
  topology, which sits poorly with a course whose Chapter 3 is HDFS.
- *Approach 2 — HDFS only.* Most literal rubric reading. The
  dashboard JSON would have to live on HDFS, tying it to a running
  cluster and inflating monthly paused cost from ~\$1 to ~\$40 in
  EBS provisioning.
- *Approach 3 — Hybrid: HDFS for compute, S3 for serving + raw
  archive.* Two storage systems to operate, three extra manifests
  (NameNode StatefulSet, DataNode StatefulSet, `hdfs-env`
  ConfigMap), but each side does what it's best at.

**What we shipped.**
HDFS via `infra/cloud/k8s-overlays/hdfs.yaml`: one NameNode on a 5
GiB EBS volume, two DataNodes on 20 GiB EBS volumes each, RF=2, 64
MiB block, 40 GiB total capacity. Bronze, silver, gold, and the
speed-layer checkpoints all live on HDFS. The 17.05 GiB raw EDF
archive lives at
`s3://brainwatch-capstone-923884399064/raw_edf/`; the dashboard
payload at `s3://brainwatch-dashboard-923884399064/`. The bronze
streamer (`scripts/bronze_stream_from_s3.py`) caps its archive at
`ARCHIVE_RAW_CAP_GIB=4` so the RF=2 footprint stays under 50% of the
40 GiB cluster capacity. Measured outcome: HDFS held 9.4 GiB at peak
across replicas; bronze grew from 22 MiB to 3.4 GiB as the streamer
worked the cohort; silver and gold rebuilt to 0.87 MiB and 16.9 KiB
respectively per CronJob fire. The dashboard kept rendering across
two teardown-resume cycles because S3 served the JSON without
needing the cluster.

**Takeaway in one sentence.**
*Compute is ephemeral; data is durable.* Where the consumer of a
piece of data does not need it co-located with compute, S3 is the
right home; where Spark wants data locality, HDFS is.

**Where to look in the repo.**

- HDFS manifest (NameNode + 2 DataNodes, RF=2) →
  `infra/cloud/k8s-overlays/hdfs.yaml`
- Bronze streamer with archive cap →
  `scripts/bronze_stream_from_s3.py` (`ARCHIVE_RAW_CAP_GIB`)
- Bronze-streamer Deployment manifest →
  `infra/cloud/k8s-overlays/bronze-streamer.yaml`
- S3 bucket reference in the cluster-state exporter →
  `scripts/cluster_state_to_s3.py`

---

### Lesson 5 — System Integration

**Problem (what hurt us).**
The deployment is ten YAML files in `infra/cloud/k8s-overlays/` with
inter-component dependencies that must hold at bring-up: HDFS
NameNode must be reachable before the bronze-loader CronJob fires;
Cassandra must be reachable before the schema-init Job runs; Kafka
must accept traffic before the producer pod sends; the cluster-state
exporter needs its ServiceAccount/RoleBindings applied before its
first `kubectl` call. Early bring-up exhibited a particularly painful
race: the loader CronJob fired before HDFS was reachable and silently
wrote to its own container's filesystem, then exited with success.
The CronJob log showed the `-put` command running;
`hdfs dfs -ls /lake/bronze` returned an empty listing; nothing on the
dashboard suggested anything was wrong. The next spark-batch fire
then read empty bronze and wrote empty silver — an end-to-end-working
pipeline producing no content.

**What we tried.**

- *Approach 1 — Helm with `post-install` hooks.* Helm has the
  mechanism but adds a templating layer that exceeds the value for a
  ten-file deployment.
- *Approach 2 — Argo Workflows as the apply DAG.* Explicit
  dependencies, but Argo itself has stateful components that must be
  brought up first. Chicken-and-egg costs are high.
- *Approach 3 — Bash apply plus `kubectl wait`, plus a self-wait
  `until` loop at the top of every CronJob.*

**What we shipped.**
`infra/cloud/resume_from_snapshots.sh` applies manifests in six
stages: cluster provision + auth, EBS CSI driver, PVs pre-bound from
snapshots, PVCs, AWS credentials Secret, then the namespaced
workloads in dependency order (Cassandra → Kafka → Grafana →
real-pipeline → HDFS overlay → batch overlay). Each CronJob's command
opens with:

```bash
HDFS="/opt/hadoop-3.2.1/bin/hdfs dfs -fs $HDFS_NN"
until $HDFS -ls / >/dev/null 2>&1; do
  echo "waiting for HDFS RPC..."; sleep 5
done
```

The streamer and the cluster-state exporter both run init containers
that pull the project wheel + scripts from S3 so the main container
doesn't need a custom image. And after the loader's `-put` step, the
`hdfs-bronze-loader` CronJob in
`infra/cloud/k8s-overlays/batch-on-hdfs.yaml` tracks which streams
had source data and asserts they all resolved to non-empty paths on
HDFS — fails loud if not (this catches the silent-success failure
mode where `hdfs dfs` falls back to the local FS). End-to-end resume
time ≈ 20 minutes.

**Takeaway in one sentence.**
*A pre-flight `until` loop is cheaper than a restart policy, and
every component whose output is invisible on the dashboard needs a
sanity check that fails the Job loudly when the output isn't where
it should be.*

**Where to look in the repo.**

- Bring-up script → `infra/cloud/resume_from_snapshots.sh`
- Self-wait loop on every CronJob →
  `infra/cloud/k8s-overlays/batch-on-hdfs.yaml`
  (`hdfs-bronze-loader` `until` block)
- Post-`-put` non-empty assertion → same file, `EXPECTED_STREAMS`
  + `$HDFS -test -d` + `$HDFS -ls` block
- Manifest validation pre-apply → `kubeconform -strict` in the
  deploy scripts

---

### Lesson 6 — Performance Optimization

**Problem (what hurt us).**
The spark-batch CronJob fires every five minutes against the live
HDFS bronze. Two successive runs on slightly different bronze sizes
both took ~50 s and ~51 s — the runtime is nearly independent of
input size, which is a clean signal that *fixed overhead dominates*.
The temptation was to attack the runtime directly (Pandas UDF
rewrite, Scala port).

**What we tried.**

- *Approach 1 — Pandas UDF for `_score`.* Save the per-row pickle
  cost. But the UDF is small and the dominant cost is outside Python.
- *Approach 2 — Port the speed layer to Scala.* Removes the Python
  interpreter, but doubles the language surface area for a
  five-person team.
- *Approach 3 — Pre-aggregate inside the streamer.* Shift work out
  of the batch path. But the watermark + late-data handling provided
  natively by Structured Streaming would have to be reimplemented.
- *Approach 4 — Leave the batch alone; tune the speed layer
  instead.* Put optimisation effort where the user notices it.

**What we shipped.**
Four specific speed-layer optimisations in
`src/brainwatch/processing/speed_layer.py`:

- `maxOffsetsPerTrigger=5000` — caps cold-start batch size so the
  first micro-batch after a restart doesn't starve executors.
- `spark.sql.shuffle.partitions=8` — keeps shuffle partitions
  proportional to micro-batch size; the default of 200 produces
  empty shuffles at our volume.
- `spark.sql.adaptive.enabled=true` — lets AQE collapse small
  partitions in the windowed-aggregation stage.
- `zlib.crc32` in the UDF (not Python's `hash()`) — Python's
  built-in `hash()` is salted per-process via `PYTHONHASHSEED`,
  which would yield non-reproducible scores across executors and
  across pod restarts.

Measured outcome: speed layer sustains 60–100 alerts per micro-batch
at p50 visibility ≈ 12 s.

**Takeaway in one sentence.**
*A fixed overhead dominates a small workload* — `spark-submit
--packages` alone takes 20–30 s and JVM startup adds 5–10 s, so
optimising the work inside the job is worthless until you account
for the fixed overhead. *Optimise the workload the user sees.*

**Where to look in the repo.**

- Speed-layer optimisations (all four) →
  `src/brainwatch/processing/speed_layer.py::build_kafka_streaming_pipeline`
- Spark batch overhead breakdown (~80% startup + packages on every
  fire) → `Empirical.tex` §4 (Spark batch fixed-overhead model) in
  the Overleaf report
- `zlib.crc32` deterministic-variance trick → the `_score` function
  in `speed_layer.py`

---

### Lesson 7 — Monitoring & Debugging

**Problem (what hurt us).**
The deployed system has twelve pods at steady state across two EKS
worker nodes. "Is the pipeline alive?" was a question that required
three or four `kubectl` commands to answer. The dashboards that
shipped first only answered "how many alerts has Cassandra absorbed?"
— silent on whether the streamer was producing, whether HDFS was
healthy, whether the CronJobs had fired on schedule, or whether the
under-replicated block count was sane. An operator who needs three
commands to confirm health will not run them as often as needed;
some failure classes go undetected for hours.

**What we tried.**

- *Approach 1 — Prometheus + the Kubernetes state-metrics
  exporter.* The right answer at scale, but two additional stateful
  services plus a non-trivial amount of YAML for a capstone.
- *Approach 2 — Hosted SaaS (Datadog or similar).* Shifts cost to a
  third party; introduces an external dependency on someone else's
  reliability for our internal view.
- *Approach 3 — Custom polling pod that writes JSON to S3.* Not as
  rich as Prometheus, but Grafana already reads JSON via the
  Infinity datasource, so the integration is zero-effort. Polling
  cadence sets a floor on freshness.

**What we shipped.**
`scripts/cluster_state_to_s3.py` runs as a Python pod with an
in-cluster ServiceAccount granted `get/list` on pods, deployments,
statefulsets, jobs, and cronjobs in the namespace and on nodes
cluster-wide. Every 30 seconds it gathers three signals:

- `kubectl get {pods,nodes,cronjobs,jobs}` → pod inventory by app,
  node readiness, CronJob fire times.
- `kubectl -n brainwatch exec sts/hdfs-namenode -- hdfs dfsadmin
  -report` → HDFS health, used capacity, under-replicated block
  count.
- `kubectl -n brainwatch exec sts/cassandra -- cqlsh -e "SELECT
  COUNT(*) ..."` → live alert count from Cassandra.

Then writes seven flat JSON files to S3, each consumed by a
panel in the Architecture Status Grafana dashboard:
`cluster_summary.json`, `cluster_pods.json`, `cluster_nodes.json`,
`cluster_cronjobs.json`, `cluster_hdfs.json`,
`cluster_hdfs_lake.json`, `pipeline_metrics.json`. Under 200 lines
of Python + one Deployment manifest; refresh ≈ 30 s.

**Takeaway in one sentence.**
*Write flat JSON, not nested* — the first exporter version wrote one
nested `cluster_state.json` and pointed Grafana's Infinity panels at
dotted paths; every stat panel showed "No data" until the file was
split. Surface failure as a visible number on the dashboard; if the
answer is on the dashboard, nobody gets paged.

**Where to look in the repo.**

- The exporter pod → `scripts/cluster_state_to_s3.py`
- Its Deployment + ServiceAccount/RoleBinding →
  `infra/cloud/k8s-overlays/cluster-state-exporter.yaml`
- Architecture Status Grafana dashboard JSON →
  `infra/cloud/grafana-architecture-status-dashboard.json`
- The seven flat JSON files in S3 →
  `s3://brainwatch-dashboard-923884399064/cluster/`

---

### Lesson 8 — Scaling

**Problem (what hurt us).**
The cohort is bounded (1,571 EDFs in S3), but the streamer's local
cadence is configurable. The demo requires roughly half the cohort
pre-loaded into bronze quickly, then a slower cadence so the rest
arrives visibly during the presentation. A single streamer pod with
`SLEEP_BETWEEN_EDF=2` saturated the pod's CPU on the MNE parse and
saturated the Kafka producer on JSON serialisation, each
independently. Throughput plateaued at ~0.5 EDFs/s despite the work
being embarrassingly parallel across files. Pre-load took 13 minutes
instead of the ~6 that two cooperating workers would have taken.

**What we tried.**

- *Approach 1 — horizontal replication of the streamer pod.* Two
  pods double throughput in principle, but the streamer holds
  progress state in `/data/lake/_state/bronze_streamer.json` —
  two writers on the same file would race. Per-pod state files
  keyed by partition, or an external coordinator, would be needed.
- *Approach 2 — vertical scaling.* Bump pod CPU/memory limits. The
  node size cap is 4 vCPUs on t3.xlarge; already near the ceiling.
- *Approach 3 — Pandas UDF for the MNE parse.* Amortise the Python
  interpreter trip. But MNE is per-file, not vectorisable across
  files.
- *Approach 4 — time-box the burst.* Tune `SLEEP_BETWEEN_EDF` so a
  single streamer reaches the 50% mark inside the demo budget. A
  slightly slower pre-load in exchange for zero architectural
  change.

**What we shipped.**
For the demo: `SLEEP_BETWEEN_EDF=2` during the burst, wait until the
state file records ≥50% of the cohort, trigger the loader + batch
CronJobs manually, then reset to `SLEEP_BETWEEN_EDF=20`. The full
code path is unchanged across the two cadences; only the environment
variable differs. Measured outcome: at burst sleep, 789 EDFs in 13
minutes (~1.0 EDF/s, limited by S3 download + MNE parse); at demo
sleep, ~0.05 EDFs/s — the rate at which the dashboard's bronze size
visibly grows between refreshes.

**Takeaway in one sentence.**
*Scale only the bottleneck.* The streamer was the bottleneck for the
burst load; the speed layer was the bottleneck for steady-state
alert volume; horizontally scaling the batch driver would have
wasted compute without moving any user-visible number. Per-partition
state is the prerequisite for ever replicating the streamer.

**Where to look in the repo.**

- Streamer with configurable cadence → `scripts/bronze_stream_from_s3.py`
  (`SLEEP_BETWEEN_EDF` env var)
- Streamer Deployment manifest →
  `infra/cloud/k8s-overlays/bronze-streamer.yaml`
- Progress state file path → `/data/lake/_state/bronze_streamer.json`
  on the bronze PVC
- Single-writer guarantee → the Deployment uses
  `strategy: {type: Recreate}` so two replicas can never coexist on
  the same PVC

---

### Lesson 9 — Data Quality & Testing

**Problem (what hurt us).**
The project carries 110+ pytest cases (all passing) plus
`kubeconform` validation of every Kubernetes manifest. The discipline
catches a substantial class of regressions before they reach the
cluster. Two integration bugs slipped past it because they lived at
the boundary between Python and Spark:

1. `silver_layer._read_bronze` used `os.walk` to sniff bronze
   format. `os.walk` does *not* work against `hdfs://` URIs, so the
   function silently fell back to the Parquet reader, which crashed
   on the JSONL payload with `CANNOT_READ_FILE_FOOTER`.
2. The bronze loader copied the streamer's nested directory verbatim
   into HDFS, producing `/lake/bronze/bronze_real/eeg/` where silver
   reads from `/lake/bronze/eeg/`.

The first surfaced as a hard crash and was fixed within an hour.
The second was silent: silver and gold stayed at the initial-cohort
values for several CronJob fires before the dashboard's lake-zone
bar gauge made the absence visible.

**What we tried.**

- *Approach 1 — full end-to-end integration test in CI.* A Docker
  Compose stack with HDFS, Kafka, Cassandra, and a synthetic EDF
  stream, exercised by every PR. Build time ~2 days + CI time
  10–15 min per PR; outside the capstone budget.
- *Approach 2 — plan-inspection tests.* Assert that a specific
  Spark operator appears in the executed plan; brittle across Spark
  version renames; value is a class of regression that
  output-correctness tests miss.
- *Approach 3 — dashboard-as-test.* Surface every integration
  contract as a visible number on the dashboard, so a zero or stale
  value is louder than a log line that scrolls past. Per-contract
  panel work, paid once.

**What we shipped.**
Unit-test discipline holds (110+ functions, all passing).
Plan-inspection test for the broadcast hint lives in
`tests/test_gold_layer.py::test_patient_dim_join_uses_broadcast`.
`kubeconform -strict` validates every overlay before `kubectl apply`
and caught two malformed selectors during the hybrid roll-out. The
two latent bugs were fixed at root: `_read_bronze` now uses the
Hadoop `FileSystem` via Spark's JVM gateway to list non-local URIs,
and the bronze loader's promote loop maps streamer paths into the
canonical bronze paths (the `for stream in eeg edf` block in
`infra/cloud/k8s-overlays/batch-on-hdfs.yaml`). Measured outcome:
after the fix, silver grew from 0.49 MiB to 0.87 MiB on the next
CronJob fire, confirming silver was reading the streamer's new
bronze.

**Takeaway in one sentence.**
*Tests and observability do not substitute for each other — each
catches a different class of regression.* Plan inspection is its own
test category; add a dashboard-as-test counter for every integration
contract whose violation would otherwise be silent.

**Where to look in the repo.**

- All 24 test files (110+ functions) → `tests/`
- Plan-inspection test for broadcast →
  `tests/test_gold_layer.py::test_patient_dim_join_uses_broadcast`
- `_read_bronze` Hadoop-FileSystem fix →
  `src/brainwatch/processing/silver_layer.py`
- Bronze-loader promote loop →
  `infra/cloud/k8s-overlays/batch-on-hdfs.yaml`
  (`hdfs-bronze-loader` CronJob, `for stream in eeg edf` block)

---

### Lesson 10 — Security & Governance

**Problem (what hurt us).**
The capstone is not a production medical platform; HIPAA-grade
controls are out of scope. But the team handles three sensitive
items: an AWS root access key, a GitHub personal access token, and
a BDSP credentialed-access key for the EEG corpus. A leaked AWS key
can cost thousands of US dollars before billing alerts fire; a
leaked BDSP key violates the data-use agreement; a leaked GitHub
token pollutes the repository's trust boundary. Three different leak
surfaces share one root cause: secrets touching developer machines
without discipline.

**What we tried.**

- *Approach 1 — AWS IAM Roles for Service Accounts (IRSA).* Pods
  assume short-lived roles via an OIDC trust policy bound to the
  ServiceAccount; no long-lived key on disk. OIDC provider setup +
  trust policy per role + moderate learning curve.
- *Approach 2 — HashiCorp Vault.* Per-secret leasing and audit;
  stateful service that must itself be brought up reliably;
  chicken-and-egg cost too high for the capstone.
- *Approach 3 — Single Kubernetes Secret per logical credential.*
  Pragmatic for the capstone, inadequate for production. No
  automatic rotation, no per-pod credential scoping.

**What we shipped.**
The AWS root key is loaded once at apply time into the
`aws-credentials` Secret in the `brainwatch` namespace; pods
reference it through `secretKeyRef`, so values are not baked into
manifests. The BDSP key file `rootkey.csv` lives in a directory that
is the *parent* of the repository working tree, so it cannot be
tracked by the repository's `.git` regardless of staging mistakes.
Data flows only to the project S3 bucket
`s3://brainwatch-capstone-923884399064/raw_edf/`; EDFs are not
redistributed beyond the team. A repository-wide audit at the time
of writing returned the empty set for any file ever added under
`credentials/` or matching `rootkey.csv`; the four
`AKIA[A-Z0-9]{16}` matches across the full history are all the
AWS-documented example key `AKIAIOSFODNN7EXAMPLE` used in a single
test fixture. No real credential has been committed.

**Takeaway in one sentence.**
*A small set of well-scoped secrets is the realistic ceiling for a
student project* — IRSA, KMS-encrypted PVCs, CloudTrail with
seven-year retention, PHI tokenisation, and a Business Associate
Agreement are the production additions described in the Vision
chapter. A `git log -p` audit before publishing is a five-second
discipline that prevents the most embarrassing public-repository
incident.

**Where to look in the repo.**

- `aws-credentials` Secret reference →
  `infra/cloud/k8s-overlays/real-pipeline.yaml` (`secretKeyRef`
  blocks)
- BDSP key path (deliberately outside the repo) →
  `../credentials/rootkey.csv` (parent of the repo working tree)
- `.gitignore` entries that enforce the credential boundary →
  `.gitignore`
- The git-log audit commands (run before publishing) →
  `docs/RUBRIC-COVERAGE.md` and the report's Reflections L10

---

### Lesson 11 — Fault Tolerance

**Problem (what hurt us).**
The failure modes that matter for the capstone are: an EKS worker
node terminating mid-batch, the NameNode pod restarting under
memory pressure, Cassandra losing its EBS volume, Kafka losing its
EBS volume, and the entire cluster being torn down between demos.
The most disruptive is the pause-resume cycle: a clean
`eksctl delete cluster` returns the EBS volumes to the detached
state, and the EBS snapshots taken at pause time are the only
durable representation of the cluster state. A pause that loses data
would make the demo non-repeatable; a pod restart that loses
checkpoint state would force the speed layer to reprocess from the
topic start with duplicate alerts that the Cassandra primary key
may or may not absorb.

**What we tried.**

- *Approach 1 — HDFS HA with QJM.* Two NameNodes + three
  JournalNodes + ZooKeeper for automatic failover. The correct
  production answer; five additional pods of operational surface.
- *Approach 2 — Cassandra RF=3 across three availability zones.*
  Standard production posture; two extra pods + repair/compaction
  tuning.
- *Approach 3 — Kafka with three brokers + in-sync-replica
  enforcement.* Standard production posture; two extra pods +
  broker-id management + leader election under partial failure.
- *Approach 4 — Accept single points of failure; rely on PVC
  reattachment + snapshot-based pause-resume.* Pragmatic, within
  budget. An EKS worker terminating mid-batch requires manual
  investigation.

**What we shipped.**
Single-instance NameNode, single-instance Cassandra (`RF=1`,
`SimpleStrategy`), single-instance Kafka (KRaft mode). Every
stateful pod has an EBS-backed PVC; the StatefulSet pattern
reattaches a restarted pod to its prior volume by name. The
speed-layer Spark Structured Streaming pipeline checkpoints to
`/data/checkpoints/kafka_speed_layer` on `checkpoints-pvc`. The
pause-resume cycle is script-driven: pause produces 8 EBS snapshots
catalogued in `artifacts/eks/snapshots/index.txt`; resume runs
`infra/cloud/resume_from_snapshots.sh`. The pod-delete protocol in
`scripts/verify_exactly_once.sh` exercises the same recovery path
on the speed layer (snapshot Cassandra alert count, force-delete the
speed-layer pod mid-batch, wait for the Deployment to recreate it,
assert no row regression after two micro-batches). End-to-end
cluster resume time from `eksctl create cluster` to "all pods
Running" is approximately 20 minutes.

**Takeaway in one sentence.**
*A capstone's fault-tolerance posture is what its pause-resume cycle
survives* — if the system can be torn down and brought back from
snapshots in under 30 minutes with no data loss, the same machinery
covers the routine pod-restart case at no extra cost. Design the
serving-store primary key so a replayed insert is a no-op upsert.

**Where to look in the repo.**

- StatefulSets that reattach by name → `infra/cloud/k8s-overlays/hdfs.yaml`,
  `infra/k8s/cassandra-statefulset.yaml`,
  `infra/cloud/k8s-overlays/kafka-kraft.yaml`
- Speed-layer checkpoint location →
  `src/brainwatch/processing/speed_layer.py` (`checkpointLocation`
  option in the `writeStream`)
- Pause-resume script → `infra/cloud/resume_from_snapshots.sh`
- EBS snapshot inventory (8 snapshots, PVC → volume → snapshot id)
  → `artifacts/eks/snapshots/index.txt`
- Pod-delete exactly-once test → `scripts/verify_exactly_once.sh`

---

**You are ready.** If you only remember three things from this document:

1. **Lambda = batch + speed over the same data**, merged at serving — that's
   the rubric, that's our architecture.
2. **Bronze JSONL → Silver Parquet → Gold Parquet**, with a Kafka/Spark/Cassandra
   speed path running in parallel, all on EKS.
3. **Costs go to zero** when the cluster is deleted because the **dashboard
   lives on S3** and the **data lives on EBS snapshots** — resume in 15 minutes.

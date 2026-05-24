# BrainWatch — Big Data Platform for Real-Time EEG Monitoring

> Lambda-architecture big data system for near-real-time EEG anomaly detection with EHR context enrichment.

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![Spark 3.5](https://img.shields.io/badge/spark-3.5-orange)](https://spark.apache.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

## Overview

BrainWatch implements a full Lambda-architecture pipeline for hospital-scale EEG monitoring. The system ingests multi-site clinical EEG recordings alongside asynchronous EHR updates, performs both batch and streaming analytics via Apache Spark, and generates near-real-time anomaly alerts through a rule-based serving layer.

**Data context.** Source metadata is derived from the BDSP clinical EEG corpus. The implementation operates on a locally staged subset enabling reproducible development without cloud dependencies.

## Quick Start

```bash
# Setup
source /mnt/disk1/aiotlab/envs/uffm/bin/activate
pip install -e ".[dev,spark,kafka]"
```

### 1. Generate EEG manifest

```bash
python scripts/download_eeg_ehr.py \
  --csv-dir ../STELAR-private/pretrain/reve/metadata \
  --output artifacts/week2/download_manifest.json \
  --target-hours 50
```

### 2. Replay events

```bash
# With Kafka (Docker):
python scripts/replay_to_kafka.py \
  --manifest artifacts/week2/download_manifest.json \
  --bootstrap-servers localhost:9094

# File fallback:
python scripts/replay_to_kafka.py \
  --manifest artifacts/week2/download_manifest.json \
  --fallback
```

### 3. Start local stack

```bash
docker compose -f infra/docker/docker-compose.yml up -d
# Kafka UI: http://localhost:8890
# Spark UI: http://localhost:8891
```

### 4. Run tests

```bash
pytest -v   # 67 tests passing
```

### 5. Deploy to Kubernetes

```bash
bash infra/k8s/deploy.sh
# View status: kubectl get all -n brainwatch
```

## Repository Structure

```
Big-Data-Project/
├── configs/              # Configuration templates
├── docs/                 # Architecture, setup, technology docs
│   └── TECHNOLOGY.md     # Comprehensive architecture documentation
├── infra/
│   ├── docker/           # Docker Compose (Kafka KRaft + Spark)
│   └── k8s/              # Kubernetes manifests
├── scripts/              # CLI tools
│   ├── download_eeg_ehr.py    # EEG download + EHR generation
│   └── replay_to_kafka.py     # Event replay simulator
├── src/brainwatch/       # Core package
│   ├── contracts/        # Event schemas
│   ├── ingestion/        # Producers, writers, DLQ
│   ├── processing/       # Spark pipelines
│   └── serving/          # Anomaly rules, alert publisher
├── tests/                # 67 unit tests
└── dashboard/            # React frontend (WIP)
```

## Technology Stack

| Component | Technology | Purpose |
|-----------|------------|---------|
| Message Bus | Apache Kafka 3.9 (KRaft) | No ZooKeeper |
| Stream Processing | Apache Spark 3.5 | Batch + Structured Streaming + MLlib |
| **Distributed Storage** | **HDFS 3.2 (1 NN + 2 DN, RF=2)** | Bronze/silver/gold lake + Spark checkpoints |
| **Raw archive** | **S3 `raw_edf/` (17 GiB / 1,571 EDFs)** | Source of truth the bronze-streamer reads from |
| **Object Storage** | **S3 static-website bucket** | Dashboard rollups, survives cluster teardown |
| **Bronze production** | `bronze-streamer` Deployment | S3 EDF → mne parse → JSONL → HDFS, continuous |
| **Batch trigger** | K8s CronJobs (every 5 min) | `hdfs-bronze-loader` + `spark-batch-hdfs` |
| **Architecture visibility** | `cluster-state-exporter` + Grafana dashboard #6 | live kubectl + HDFS + Cassandra → S3 |
| Serving Store | Cassandra 4.1 | Alert persistence (PK `patient_id`) |
| Orchestration | Kubernetes 1.30 (AWS EKS) | Deployment |

> **Hybrid storage:** HDFS is the *compute-side* distributed filesystem
> (literal "HDFS" for the rubric); S3 is the *serving-side* object store so
> the dashboard keeps working even when the cluster is torn down for
> $1/month. See [`docs/QA-BANK.md` §17](docs/QA-BANK.md) for the full Q&A.

## Architecture

```
EEG Source → Kafka (eeg.raw) → Spark Streaming → Bronze Parquet → Speed Layer → Alerts → Cassandra
EHR Source → Kafka (ehr.updates) → Spark Streaming → Bronze Parquet ──────────┘
```

**Key features:**
- Watermarks: 10 min (EEG), 30 min (EHR)
- Deduplication via SHA256 fingerprint
- Dead-letter queue for failed validations
- Dual-sink alerts (Cassandra + Kafka)

## Team & Roles

| Member | Role |
|--------|------|
| Quang-Hung | Lead / Architect |
| Kim-Hung | Engineer (Kafka, Cassandra, Bronze) |
| Kim-Quan | Engineer (EHR, Anomaly Rules) |
| Dat | Engineer (DLQ, K8s) |
| Trang | Engineer (Tests, CLI) |

## Documentation

| Doc | When to open it |
|---|---|
| [docs/STUDY-GUIDE.md](docs/STUDY-GUIDE.md) | Read first — prereq materials + code walk + deploy + Q&A |
| [docs/CHEATSHEET.md](docs/CHEATSHEET.md) | One-page printable for the defense |
| [docs/QA-BANK.md](docs/QA-BANK.md) | Every question consolidated — 17 sections incl. hybrid HDFS+S3 |
| [docs/PYSPARK-STREAMING-QA.md](docs/PYSPARK-STREAMING-QA.md) | 80 streaming-engine Q&As |
| [docs/AUTO-TRIGGER-MECHANISMS.md](docs/AUTO-TRIGGER-MECHANISMS.md) | CronJob vs streaming vs event-driven |
| [docs/REAL-VS-DEMO.md](docs/REAL-VS-DEMO.md) | What a real hospital deployment adds |
| [docs/TECHNOLOGY.md](docs/TECHNOLOGY.md) | Full architecture documentation |
| [docs/PRESENTATION-GUIDE.md](docs/PRESENTATION-GUIDE.md) | Defense pitch + "if they ask" boxes |
| [docs/final-report.md](docs/final-report.md) | The formal write-up |
| [docs/setup-guide.md](docs/setup-guide.md) | Environment setup instructions |

## Status — final

- **Architecture + ingestion + batch + speed + serving**: complete
- **Real data**: 8.5 GiB of real BDSP/Harvard EDF (1,190 recordings, 1,097
  patients, 4 sites) parsed with `mne` into measured bronze events; real
  HEEDB ICD-10 neurology diagnoses joined for 640 patients
- **Tests**: 131 passing (`pytest -q`), local-first
- **Cloud**: deployed end-to-end on AWS EKS — Kafka (KRaft) → Spark Structured
  Streaming → Cassandra → S3 → Grafana (4 dashboards)
- **Docs**: `docs/PRESENTATION-GUIDE.md` (defense prep), `docs/final-report.md`
  (11-lesson rubric), `CONTRIBUTORS.md` (role attribution)

### Real-data pipeline (local)

```bash
# 1. Download real EDF from BDSP (needs the rootkey)
export BDSP_CREDENTIALS=../credentials/rootkey.csv
python scripts/download_real_edf.py --target-gb 8.5 --min-duration 600 --max-duration 3000

# 2. Parse real signal → measured bronze events
python scripts/edf_to_bronze.py --bronze data/lake/bronze_real

# 3. Real EHR with HEEDB ICD-10
python scripts/build_real_ehr.py --bronze data/lake/bronze_real

# 4. Batch bronze → silver → gold
python scripts/run_batch.py --bronze data/lake/bronze_real --silver data/lake/silver_real --gold data/lake/gold_real

# 5. Clinical insights (real ICD-10) + alerts
python scripts/extract_clinical_insights.py --silver data/lake/silver_real --gold data/lake/gold_real --alerts artifacts/demo/alerts_real.jsonl
```

## License

MIT License - see LICENSE file
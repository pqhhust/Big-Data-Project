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
| Message Bus | Apache Kafka 3.9 | KRaft mode, no Zookeeper |
| Stream Processing | Apache Spark 3.5 | Structured Streaming |
| Serving Store | Cassandra 4.1 | Alert persistence |
| Orchestration | Kubernetes 1.28+ | Deployment |

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

- [Technology Architecture](docs/TECHNOLOGY.md) - Full architecture documentation
- [Setup Guide](docs/setup-guide.md) - Environment setup instructions
- [Week 1 Slides](docs/week1-slides.md) - Week 1 deliverables presentation

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
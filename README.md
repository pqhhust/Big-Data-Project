# BrainWatch — Big Data Platform for Real-Time EEG Monitoring

> Lambda-architecture big data system for near-real-time EEG anomaly detection with EHR context enrichment.

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![Spark 3.5](https://img.shields.io/badge/spark-3.5-orange)](https://spark.apache.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

## Overview

BrainWatch is a 6-week milestone project that implements a full Lambda-architecture pipeline for hospital-scale EEG monitoring. The system ingests multi-site clinical EEG recordings alongside asynchronous EHR updates, performs both batch and streaming analytics via Apache Spark, and generates near-real-time anomaly alerts through a rule-based serving layer.

**Data context.** Source metadata is derived from the BDSP clinical EEG corpus (multi-site, multi-service). The implementation operates on a locally staged subset (~few GB) rather than direct AWS S3 reads, enabling reproducible development without cloud dependencies.

## Key Components

| Layer | Technology | Description |
|---|---|---|
| **Ingestion** | Python, Kafka | EEG metadata profiling, subset selection, event publishing |
| **Batch** | PySpark | Bronze → Silver → Gold data lake transformations |
| **Speed** | Spark Structured Streaming | Watermarked stream joins, stateful aggregations, alert generation |
| **Serving** | Cassandra / MongoDB | Low-latency alert & patient-state lookup |
| **Deployment** | Kubernetes | Namespace isolation, ConfigMaps, Spark job templates |

## Repository Structure

```
Big-Data-Project/
├── configs/              # Environment and pipeline configuration
├── docs/                 # Architecture design, checklists, setup guide
├── infra/
│   ├── docker/           # Docker Compose (Kafka + Spark local stack)
│   └── k8s/              # Kubernetes deployment manifests
├── scripts/              # CLI entry points
│   ├── download_bdsp_subset.py     # ~100h download manifest builder
│   ├── replay_to_kafka.py          # Unified EEG + EHR replay simulator
│   ├── profile_eeg_metadata.py     # Metadata profiling
│   └── build_local_subset_manifest.py
├── src/brainwatch/       # Core Python package
│   ├── config/           #   Settings loader
│   ├── contracts/        #   Canonical event schemas (EEG, EHR, Feature, Alert)
│   ├── ingestion/        #   Producers, normalisers, bronze writer, DLQ
│   ├── processing/       #   Spark batch & streaming pipelines
│   └── serving/          #   Anomaly classification rules
├── tests/                # 24 unit tests
└── artifacts/
    ├── week1/            # Metadata profile + subset manifest
    └── week2/            # Download manifest + event replay
```

## Quick Start

```bash
# Setup (uses uffm conda env)
conda activate uffm
pip install -e ".[dev]"

# Full setup guide: docs/setup-guide.md
```

### 1. Generate 100h download manifest (5 BDSP sites, short recordings)

```bash
python scripts/download_bdsp_subset.py \
  --csv-dir ../STELAR-private/pretrain/reve/metadata \
  --output artifacts/week2/download_manifest.json \
  --target-hours 100
```

### 2. Replay events (file fallback — no Kafka needed)

```bash
python scripts/replay_to_kafka.py \
  --manifest artifacts/week2/download_manifest.json \
  --fallback
```

### 3. Start local Kafka stack (optional)

```bash
docker compose -f infra/docker/docker-compose.yml up -d
# Kafka UI: http://localhost:8080
```

### 4. Run tests

```bash
pytest -v   # 24 tests, all pass without Docker/Kafka
```

## Week 1 Deliverables

| Deliverable | Status | Location |
|---|---|---|
| Architecture decision (Lambda vs Kappa) | Done | [`docs/architecture.md`](docs/architecture.md) |
| EEG metadata inventory utility | Done | [`src/brainwatch/ingestion/eeg_inventory.py`](src/brainwatch/ingestion/eeg_inventory.py) |
| Local subset manifest builder | Done | [`src/brainwatch/ingestion/subset_manifest.py`](src/brainwatch/ingestion/subset_manifest.py) |
| Canonical event contracts | Done | [`src/brainwatch/contracts/events.py`](src/brainwatch/contracts/events.py) |
| Spark batch & streaming scaffold | Done | [`src/brainwatch/processing/spark_pipeline.py`](src/brainwatch/processing/spark_pipeline.py) |
| Anomaly classification rules | Done | [`src/brainwatch/serving/anomaly_rules.py`](src/brainwatch/serving/anomaly_rules.py) |
| Kubernetes baseline manifests | Done | [`infra/k8s/`](infra/k8s/) |
| Configuration template | Done | [`configs/project.example.yaml`](configs/project.example.yaml) |
| Unit tests (5 cases) | Done | [`tests/`](tests/) |
| Metadata profile artifact | Done | [`artifacts/week1/eeg_metadata_profile.json`](artifacts/week1/eeg_metadata_profile.json) |
| Subset manifest artifact | Done | [`artifacts/week1/eeg_subset_manifest.json`](artifacts/week1/eeg_subset_manifest.json) |

## Week 2 Deliverables

| Deliverable | Status | Location |
|---|---|---|
| 100h download manifest (1,436 subjects, 5 sites) | Done | [`artifacts/week2/download_manifest.json`](artifacts/week2/download_manifest.json) |
| EEG event producer (Kafka + file fallback) | Done | [`src/brainwatch/ingestion/eeg_producer.py`](src/brainwatch/ingestion/eeg_producer.py) |
| Synthetic EHR generator + normaliser | Done | [`src/brainwatch/ingestion/ehr_normalizer.py`](src/brainwatch/ingestion/ehr_normalizer.py) |
| Kafka helpers + FileProducer fallback | Done | [`src/brainwatch/ingestion/kafka_helpers.py`](src/brainwatch/ingestion/kafka_helpers.py) |
| Bronze zone writer with dedup | Done | [`src/brainwatch/ingestion/bronze_writer.py`](src/brainwatch/ingestion/bronze_writer.py) |
| Dead-letter queue | Done | [`src/brainwatch/ingestion/dead_letter.py`](src/brainwatch/ingestion/dead_letter.py) |
| Structured Streaming: Kafka → Bronze | Done | [`src/brainwatch/processing/bronze_ingest.py`](src/brainwatch/processing/bronze_ingest.py) |
| Unified replay simulator | Done | [`scripts/replay_to_kafka.py`](scripts/replay_to_kafka.py) |
| Docker Compose (Kafka + Spark) | Done | [`infra/docker/docker-compose.yml`](infra/docker/docker-compose.yml) |
| K8s: Kafka StatefulSet + Zookeeper | Done | [`infra/k8s/`](infra/k8s/) |
| 24 unit tests (all passing) | Done | [`tests/`](tests/) |

## Roadmap

| Week | Focus | Status |
|---|---|---|
| 1 | Foundation, architecture, project scaffold | **Complete** |
| 2 | EEG/EHR ingestion layer & Kafka integration | **Complete** |
| 3 | Batch layer — Bronze/Silver/Gold Spark jobs | Planned |
| 4 | Speed layer — Structured Streaming & alerts | Planned |
| 5 | Serving layer, dashboards, hardening | Planned |
| 6 | End-to-end integration, report, demo | Planned |

## Notes

- PySpark and kafka-python are optional dependencies — all tests pass without them.
- The detailed internal 6-week execution plan is maintained locally and excluded from version control.
- See [`docs/setup-guide.md`](docs/setup-guide.md) for full environment setup instructions.

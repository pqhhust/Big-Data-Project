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
├── docs/                 # Architecture design & execution checklists
├── infra/k8s/            # Kubernetes deployment manifests
├── scripts/              # CLI entry points for data utilities
├── src/brainwatch/       # Core Python package
│   ├── config/           #   Settings loader
│   ├── contracts/        #   Canonical event schemas (EEG, EHR, Feature, Alert)
│   ├── ingestion/        #   Metadata profiler & subset manifest builder
│   ├── processing/       #   Spark batch & streaming pipeline scaffolds
│   └── serving/          #   Anomaly classification rules
├── tests/                # Unit tests
└── artifacts/week1/      # Generated profiling & manifest outputs
```

## Quick Start

### 1. Profile EEG metadata

```bash
python scripts/profile_eeg_metadata.py \
  --csv <path-to-site-metadata-1>.csv \
  --csv <path-to-site-metadata-2>.csv \
  --output artifacts/week1/eeg_metadata_profile.json
```

### 2. Build local subset manifest

```bash
python scripts/build_local_subset_manifest.py \
  --csv <path-to-site-metadata>.csv \
  --output artifacts/week1/eeg_subset_manifest.json \
  --max-sessions 12 \
  --target-hours 8
```

### 3. Run tests

```bash
pytest
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

## Roadmap

| Week | Focus | Status |
|---|---|---|
| 1 | Foundation, architecture, project scaffold | **Complete** |
| 2 | EEG/EHR ingestion layer & Kafka integration | Planned |
| 3 | Batch layer — Bronze/Silver/Gold Spark jobs | Planned |
| 4 | Speed layer — Structured Streaming & alerts | Planned |
| 5 | Serving layer, dashboards, hardening | Planned |
| 6 | End-to-end integration, report, demo | Planned |

## Notes

- PySpark is listed as an optional dependency; the Spark scaffold is validated structurally in Week 1 and will be executed against a live cluster from Week 2 onward.
- The detailed internal 6-week execution plan is maintained locally and excluded from version control.

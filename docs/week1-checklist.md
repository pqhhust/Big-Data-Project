# Week 1 — Deliverable Checklist

> All items below are committed to the repository as part of the Week 1 milestone.

## Member 1: Project Lead & Report Owner

- [x] Architecture decision finalized — Lambda selected with documented rationale
- [x] Problem statement framed and aligned with milestone requirements
- [x] Report-oriented architecture design document authored
- [x] 6-week execution plan with owner matrix established

## Member 2: EEG Data Engineer

- [x] EEG metadata inventory utility implemented (`eeg_inventory.py`)
- [x] Local EEG subset manifest builder implemented (`subset_manifest.py`)
- [x] Multi-site metadata profiling artifact generated (5 sites, 306k+ rows)
- [x] Data quality rules documented (missing duration, short sessions, invalid rows)
- [x] STELAR metadata schema assumptions documented

## Member 3: EHR & Feature Engineer

- [x] Canonical event contracts defined (`EEGChunkEvent`, `EHREvent`, `FeatureEvent`, `AlertEvent`)
- [x] EHR source assumptions captured in example configuration
- [x] Join strategy and serving payload structure established
- [x] Local subset-based pipeline assumptions documented

## Member 4: Spark & Streaming Engineer

- [x] Spark batch processing scaffold created (`spark_pipeline.py`)
- [x] Spark Structured Streaming skeleton with watermarking and joins
- [x] Kafka topic contracts reflected in config and code
- [x] Course-required Spark techniques mapped to concrete implementations

## Member 5: Infrastructure & Integration Engineer

- [x] Repository scaffold with Python package structure
- [x] Kubernetes baseline manifests (namespace, ConfigMap, Spark job template)
- [x] Configuration template with all environment parameters
- [x] Unit test suite (5 test cases across 3 modules)
- [x] Developer tooling and CI-ready project setup (`pyproject.toml`)

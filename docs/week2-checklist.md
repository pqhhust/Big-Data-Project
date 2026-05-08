# Week 2 — Ingestion Layer Checklist

## M1 — Project Lead
- [x] Download strategy: short recordings (1–20 min), stratified across 5 sites, ~100 hours
- [x] Naming conventions: bronze zone partitioning (site/date), JSONL format
- [x] Manifest schema v2.0 with summary statistics

## M2 — EEG Data Engineer
- [x] `scripts/download_bdsp_subset.py` — 100h manifest from 5 BDSP sites (1,436 subjects)
- [x] `src/brainwatch/ingestion/eeg_producer.py` — EEG metadata-to-event publisher
- [x] Bronze zone session manifests via `BronzeWriter`
- [x] Duplicate detection via SHA-256 fingerprint (patient + session + time)
- [x] Dead-letter routing for invalid events

## M3 — EHR & Feature Engineer
- [x] `src/brainwatch/ingestion/ehr_normalizer.py` — Synthetic EHR generation + normalisation
- [x] `normalise_raw_ehr()` — Schema validation with flexible field mapping
- [x] `write_ehr_bronze()` — Bronze zone JSONL writer
- [x] Deterministic seed per subject for reproducible synthetic data

## M4 — Spark & Streaming Engineer
- [x] `src/brainwatch/ingestion/kafka_helpers.py` — Producer/consumer factory + FileProducer fallback
- [x] `scripts/replay_to_kafka.py` — Unified EEG + EHR replay simulator
- [x] `src/brainwatch/processing/bronze_ingest.py` — Structured Streaming Kafka → Bronze Parquet
- [x] Watermarking strategy: 10 min EEG, 30 min EHR
- [x] Checkpointing for exactly-once semantics

## M5 — Infrastructure Engineer
- [x] `infra/docker/docker-compose.yml` — Kafka + Zookeeper + Spark + Kafka UI
- [x] `infra/k8s/kafka-statefulset.yaml` — Kafka on Kubernetes
- [x] `infra/k8s/zookeeper-deployment.yaml` — Zookeeper on Kubernetes
- [x] Updated `configmap.yaml` with Kafka bootstrap servers + checkpoint prefix
- [x] Updated `spark-streaming-job.yaml` with bronze ingest job

## Tests
- [x] 24 tests passing (5 Week-1 + 19 Week-2)
- [x] `test_download_bdsp_subset.py` — candidate filtering, stratified selection, manifest writing
- [x] `test_eeg_producer.py` — manifest parsing, file-fallback publishing
- [x] `test_ehr_normalizer.py` — deterministic generation, normalisation, bronze writing
- [x] `test_bronze_writer.py` — partitioned writes, dedup, DLQ routing
- [x] `test_dead_letter.py` — routing, read-back, empty state
- [x] `test_kafka_helpers.py` — serialisation roundtrip, FileProducer

## Generated Artifacts
- [x] `artifacts/week2/download_manifest.json` — 1,436 subjects, 100.03h, 1,655 S3 files
- [x] `artifacts/week2/kafka_fallback.jsonl` — 8,616 events (1,436 EEG + 7,180 EHR)

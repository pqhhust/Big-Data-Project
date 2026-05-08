# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project: BrainWatch — Lambda-Architecture EEG Big Data Platform

6-week milestone project: hospital-scale EEG monitoring with EHR enrichment via Kafka → Spark (batch + streaming) → Cassandra/MongoDB, deployed on Kubernetes. Source data is the BDSP clinical EEG corpus, operated on a locally staged subset (no live S3 reads).

## Commands

```bash
# Environment (uses shared uffm conda env, Python 3.11)
conda activate uffm
pip install -e ".[dev]"            # core + pytest only — Spark/Kafka are optional extras
pip install -e ".[dev,kafka]"      # add kafka-python
pip install -e ".[dev,spark]"      # add pyspark 3.5

# Tests (24 unit tests, all pass without Docker/Kafka/Spark)
pytest -v
pytest tests/test_bronze_writer.py::test_dedup_ignores_duplicate_events  # single test

# Generate ~100h download manifest (reads CSVs from sibling STELAR-private repo)
python scripts/download_bdsp_subset.py \
  --csv-dir ../STELAR-private/pretrain/reve/metadata \
  --output artifacts/week2/download_manifest.json --target-hours 100

# Replay events — file fallback (no Kafka required)
python scripts/replay_to_kafka.py --manifest artifacts/week2/download_manifest.json --fallback

# Local Kafka stack (KRaft mode, no Zookeeper)
docker compose -f infra/docker/docker-compose.yml up -d   # Kafka UI :8080, Spark UI :8081
# External port 9094 from host; internal 9092 inside the docker network
```

## Architecture

**Lambda architecture** chosen over Kappa because batch backfill from historical EEG/EHR is a first-class requirement; rationale and tradeoffs in `docs/architecture.md`.

### Core data flow
```
EEG/EHR sources → Kafka (eeg.raw, ehr.updates) → Spark Structured Streaming → Bronze/Silver/Gold lake
                                              → features.realtime, alerts.anomaly topics → Cassandra/MongoDB
```

### Layered package (`src/brainwatch/`)
- **`contracts/events.py`** — canonical dataclasses (`EEGChunkEvent`, `EHREvent`, `FeatureEvent`, `AlertEvent`) shared by every layer; `validate_required_fields()` is the schema gate. **Always update events here first** when adding fields, then propagate to producers, bronze writers, Spark schemas in `processing/`, and tests.
- **`ingestion/`** — Python producers that operate on download manifests:
  - `kafka_helpers.py` — `get_producer()` returns real `KafkaProducer` *or* falls back to `FileProducer` (JSONL append) if `kafka-python` import fails. The fallback is the reason the test suite passes without Docker.
  - `bronze_writer.py` — partitioned JSONL bronze sink (`stream/site=.../date=...`) with sha256 dedup fingerprint over `patient_id|session_id|event_time` and DLQ routing on missing required fields.
  - `dead_letter.py` — append-only JSONL DLQ shared by bronze writer.
  - `eeg_producer.py`, `ehr_normalizer.py` — manifest → events → publish, with `replay_speed` for time-proportional simulation.
- **`processing/`** — Spark code with **deferred PySpark imports** (imports happen inside functions). This is intentional so the package and tests work without `pyspark` installed.
  - `spark_pipeline.py` — Week-1 stream-stream join skeleton (`eeg ⨝ ehr` with watermarks 10m/30m).
  - `bronze_ingest.py` — production-shape Kafka → Bronze Parquet streams with checkpointing and a parallel DLQ stream for null-key rows.
- **`serving/anomaly_rules.py`** — pure-Python rule classifier (`classify_anomaly`) returning `AlertDecision`. Suppresses alerts when signal quality < 0.3 regardless of score.
- **`config/settings.py`** — thin YAML loader; canonical config example is `configs/project.example.yaml` (Kafka topics, lake prefixes, target hours).

### Optional dependencies pattern
`pyspark` and `kafka-python` are **optional extras**, not core deps. Code that uses them must import inside functions, never at module top-level — this is the contract that keeps `pytest` green on a fresh clone. Keep it that way when adding Spark/Kafka code.

### Test posture
Every module is tested without Docker, Kafka, or Spark. Bronze/DLQ tests use real filesystem in `tmp_path`. When adding a feature, prefer extending the existing `tests/test_*.py` file matching the module rather than creating a new one.

## Project conventions

- Topic names live in `configs/project.example.yaml` and the K8s `infra/k8s/configmap.yaml` — keep both in sync. Defaults: `eeg.raw`, `ehr.updates`, `features.realtime`, `alerts.anomaly`.
- Bronze layout: `data/lake/bronze/{eeg,ehr}/[site=.../]date=.../*.jsonl` (Python writer) or Parquet partitioned by `site_id, ingestion_date` (Spark writer). The two coexist — Python is for low-volume / dev; Spark is the production path.
- Watermarks: 10 min on EEG event_time, 30 min on EHR event_time (EHR is more bursty/late). Don't change without updating `docs/architecture.md`.
- The download manifest is the source of truth for replay — it's generated from `../STELAR-private/pretrain/reve/metadata/*_meta.csv` (sites I0002, I0003, I0009, S0001, S0002).

## What not to touch

- `data/`, `artifacts/generated/`, and `artifacts/week2/kafka_fallback.jsonl` are gitignored runtime outputs — never commit.
- `README.local-plan.md` is a local-only execution plan and is gitignored.
- The 9-channel/19-channel EEG schema (`channel_count=19`, `sampling_rate_hz=200.0`) matches the upstream pretraining pipeline; do not change defaults without checking the parent workspace's CLAUDE.md.

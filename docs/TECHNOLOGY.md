# BrainWatch Technology Architecture

A comprehensive technical documentation for the BrainWatch Lambda Architecture EEG monitoring platform.

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Technology Stack](#technology-stack)
3. [Data Flow Pipeline](#data-flow-pipeline)
4. [Component Deep Dive](#component-deep-dive)
5. [Deployment Topology](#deployment-topology)
6. [Configuration Management](#configuration-management)
7. [Testing Strategy](#testing-strategy)
8. [Team Roles](#team-roles)

---

## Architecture Overview

BrainWatch implements a **Lambda Architecture** for hospital-scale real-time EEG monitoring with EHR enrichment. The architecture combines batch and speed layers to handle both historical data backfill and real-time stream processing.

### Why Lambda Over Kappa?

| Requirement | Lambda | Kappa |
|-------------|--------|-------|
| Historical backfill from historical EEG/EHR | Native support | Complex replay |
| Real-time anomaly detection | Speed layer | Single stream |
| Debug/replay capability | Batch + speed | Complex state |
| Team parallelism | Layer-based | Sequential |

### High-Level Data Flow

```
┌───────────────────────────────────────────────────────────────────────────────┐
│                           BRAINWATCH DATA FLOW                                │
└───────────────────────────────────────────────────────────────────────────────┘

┌─────────────┐     ┌─────────────┐     ┌─────────────────────────────────────┐
│  EEG Source │     │  EHR Source │     │        Synthetic EHR                │
│  (BDSP S3)  │     │  (Hospital) │     │   (Generator for demo/testing)      │
└──────┬──────┘     └──────┬──────┘     └─────────────┬───────────────────────┘
       │                   │                          │
       │    ┌──────────────┼──────────────┐           │
       │    │              ▼              │           │
       │    │     ┌─────────────────┐     │           │
       └───>│     │   Apache Kafka  │◄────┘           │
            │     │                 │                 │
            │     │  Topics:        │                 │
            │     │  - eeg.raw      │                 │
            │     │  - ehr.updates  │                 │
            │     │  - alerts.annoy │                 │
            │     └────────┬────────┘                 │
            │              │                          │
            │    ┌─────────┴─────────┐                │
            ▼    ▼                   ▼                │
┌─────────────────┐       ┌─────────────────┐         │
│  Spark Streaming│       │  Spark Streaming│         │
│  (Bronze Layer) │       │  (Bronze Layer) │         │
│  eeg.raw -> Par │       │  ehr.updates -> │         │
│  checkpointing  │       │  Parquet        │         │
└────────┬────────┘       └────────┬────────┘         │
         │                         │                  │
         └───────────┬─────────────┘                  │
                     │                                │
                     ▼                                │
           ┌─────────────────┐                        │
           │  Bronze Zone    │                        │
           │  data/lake/     │                        │
           │  bronze/{eeg,   │                        │
           │  ehr}/          │                        │
           │  site=*/date=*  │                        │
           └────────┬────────┘                        │
                    │                                 │
                    ▼                                 │
           ┌─────────────────┐                        │
           │  Speed Layer    │◄───────────────────────┘
           │  (Real-time     │
           │   join +        │
           │   anomaly       │
           │   scoring)      │
           └────────┬────────┘
                    │
           ┌────────┴────────┐
           ▼                 ▼
    ┌─────────────┐   ┌─────────────┐
    │  Cassandra  │   │   Kafka     │
    │  (alerts)   │   │  alerts.annoy│
    └─────────────┘   └─────────────┘
```

---

## Technology Stack

### Core Infrastructure

| Component | Technology | Version | Purpose |
|-----------|------------|---------|---------|
| Message Bus | Apache Kafka | 3.9 | KRaft mode, no Zookeeper |
| Stream Processing | Apache Spark | 3.5 | Structured Streaming |
| Serving Store | Cassandra | 4.x | Alert persistence |
| Orchestration | Kubernetes | 1.28+ | Deployment & scaling |
| Container Registry | Docker Hub | - | Image hosting |

### Python Dependencies

| Package | Version | Optional | Purpose |
|---------|---------|----------|---------|
| boto3 | >=1.34 | No | S3 data download |
| PyYAML | >=6.0 | No | Configuration |
| pytest | >=8.0 | dev | Testing |
| kafka-python | - | kafka | Kafka client |
| pyspark | >=3.5,<4.0 | spark | Stream processing |
| cassandra-driver | - | serving | Cassandra client |

### Kafka Topics

| Topic | Schema | Description |
|-------|--------|-------------|
| `eeg.raw` | `EEGChunkEvent` | Raw EEG chunk events |
| `ehr.updates` | `EHREvent` | EHR event updates |
| `features.realtime` | `FeatureEvent` | Computed features |
| `alerts.anomaly` | `AlertEvent` | Anomaly alerts |

---

## Data Flow Pipeline

### Phase 1: Data Ingestion (Week 2)

**Owner:** Kim-Hung, Kim-Quan, Trang

```python
# Entry point: scripts/download_eeg_ehr.py
python scripts/download_eeg_ehr.py \
    --csv-dir ../STELAR-private/pretrain/reve/metadata \
    --output artifacts/week2/download_manifest.json \
    --target-hours 100 \
    --download --download-root data/raw/eeg
```

**Steps:**
1. **Manifest Building** (Trang): Scans BDSP metadata CSVs, selects subjects filtering by duration bounds, prioritizes shorter recordings for breadth
2. **S3 Download** (Kim-Hung): Downloads EEG EDF files via boto3, routes failures to DLQ
3. **Synthetic EHR** (Kim-Quan): Generates correlated EHR events (vital_signs, lab_result, medication, critical_lab, note)

### Phase 2: Kafka Replay (Week 2)

**Owner:** Kim-Quan, Quang-Hung

```python
# Entry point: scripts/replay_to_kafka.py
python scripts/replay_to_kafka.py \
    --manifest artifacts/week2/download_manifest.json \
    --fallback  # File-based fallback without Kafka
```

**Components:**
- `eeg_producer.py`: `manifest_to_events()` -> `EEGChunkEvent[]` -> `publish_events()`
- `ehr_normalizer.py`: `generate_ehr_from_manifest()` -> `EHREvent[]` -> `publish_ehr_events()`
- `kafka_helpers.py`: `get_producer()` with FileProducer fallback

### Phase 3: Bronze Ingestion (Week 2-3)

**Owner:** Quang-Hung

**Spark Structured Streaming:**
- Kafka -> Parquet with checkpointing
- Watermarks: 10 min (EEG), 30 min (EHR)
- Partition layout: `stream/site=*/date=YYYY-MM-DD/*.jsonl`
- DLQ routing for invalid records

### Phase 4: Speed Layer (Week 3-4)

**Owner:** Quang-Hung, Kim-Quan

**Stream-Stream Join:**
- EEG + EHR join on `patient_id` within +/-30 min window
- Watermarks prevent unbounded state
- 1-min tumbling windows, 30s slide

**Anomaly Scoring:**
```python
def compute_anomaly_score(features: dict) -> float:
    chunk_term = min(features.get("eeg_chunk_count", 0) / 60.0, 1.0)
    quality_term = 1.0 - features.get("signal_quality_score", 1.0)
    critical_term = 0.6 if features.get("has_critical_lab") else 0.0
    meds_term = min(features.get("n_medication_changes_24h", 0) / 5.0, 1.0)

    score = (0.30 * chunk_term + 0.25 * quality_term +
             0.30 * critical_term + 0.15 * meds_term)
    return max(0.0, min(score, 1.0))
```

**Severity Classification:**
| Score Range | Severity |
|-------------|----------|
| Signal quality < 0.3 | suppressed |
| >= 0.85 (or >= 0.60 + critical_lab) | critical |
| 0.65 - 0.85 | warning |
| 0.40 - 0.65 | advisory |
| < 0.40 | normal |

---

## Component Deep Dive

### Event Contracts (`contracts/events.py`)

**EEGChunkEvent:**
```python
@dataclass(slots=True)
class EEGChunkEvent:
    patient_id: str
    session_id: str
    event_time: str
    site_id: str
    channel_count: int          # 19 (10-20 montage)
    sampling_rate_hz: float     # 200.0
    window_seconds: float
    source_uri: str
```

**EHREvent:**
```python
@dataclass(slots=True)
class EHREvent:
    patient_id: str
    encounter_id: str
    event_time: str
    event_type: str             # vital_signs, lab_result, medication, critical_lab, note
    source_system: str          # epic, cerner
    version: int
    payload: dict[str, Any]
```

### Ingestion Layer (`ingestion/`)

| Module | Owner | Key Functions |
|--------|-------|---------------|
| `kafka_helpers.py` | Kim-Hung | `event_to_bytes()`, `FileProducer`, `get_producer()` |
| `bronze_writer.py` | Kim-Hung | `BronzeWriter.write_eeg()`, dedup via sha256 |
| `dead_letter.py` | Dat | `DeadLetterQueue.route()`, daily JSONL files |
| `eeg_producer.py` | Kim-Quan | `manifest_to_events()`, `publish_events()` |
| `ehr_normalizer.py` | Kim-Quan | `generate_ehr_from_manifest()`, `normalize_ehr_payload()` |

### Processing Layer (`processing/`)

| Module | Owner | Key Functions |
|--------|-------|---------------|
| `bronze_ingest.py` | Quang-Hung | `build_eeg_bronze_query()`, Kafka->Parquet |
| `speed_layer.py` | Quang-Hung | `build_streaming_pipeline()`, stream-join + anomaly UDF |
| `silver_layer.py` | Kim-Quan | Dedup, version resolution, quality flags |
| `gold_layer.py` | Kim-Quan | Broadcast joins, daily rollups |

### Serving Layer (`serving/`)

| Module | Owner | Key Functions |
|--------|-------|---------------|
| `anomaly_rules.py` | Kim-Quan | `compute_anomaly_score()`, `classify_v2()` |
| `cassandra_sink.py` | Kim-Hung | `init_keyspace()`, `write_alerts()`, `upsert_patient_state()` |
| `alert_publisher.py` | Kim-Hung | `publish_alerts()` dual-sink (Cassandra + Kafka) |

---

## Deployment Topology

### Kubernetes Resources

```
infra/k8s/
├── cassandra-statefulset.yaml    # 1-replica Cassandra with PVC (20Gi)
├── kafka-statefulset.yaml        # Kafka 3.9 KRaft (3 replicas)
├── spark-streaming-deployment.yaml  # Speed layer (long-running)
├── spark-batch-cronjob.yaml      # Daily batch at 03:00 UTC
├── configmap.yaml                # Topic names, paths, hparams
└── persistent-volumes.yaml       # PVCs: bronze, silver, gold, checkpoints
```

### Docker Compose (Local Development)

```yaml
# infra/docker/docker-compose.yml
services:
  kafka:
    image: apache/kafka:3.9.0
    ports:
      - "9094:9092"  # External
    volumes:
      - kafka-data:/var/lib/kafka/data

  kafka-ui:
    image: provectuslabs/kafka-ui:latest
    ports:
      - "8080:8080"

  spark-master:
    image: apache/spark:3.5.0
    ports:
      - "8081:8080"
```

### Data Lake Layout

```
data/lake/
├── bronze/
│   ├── eeg/
│   │   └── site=S0001/
│   │       └── date=2026-04-19/
│   │           └── eeg_bronze_20260419_120000.jsonl
│   └── ehr/
│       └── date=2026-04-19/
│           └── ehr_bronze_20260419_120000.jsonl
├── silver/
│   └── ...
├── gold/
│   └── ...
└── _dead_letter/
    └── dead_letter_2026-04-19.jsonl
```

---

## Configuration Management

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `BDSP_CREDENTIALS` | `~/credentials/rootkey.csv` | AWS root key path |
| `KAFKA_BOOTSTRAP` | `localhost:9092` | Kafka broker address |
| `CASSANDRA_HOST` | `localhost` | Cassandra contact point |

### Config File (`configs/project.example.yaml`)

```yaml
kafka:
  topics:
    eeg_raw: "eeg.raw"
    ehr_updates: "ehr.updates"
    alerts_anomaly: "alerts.anomaly"
  bootstrap_servers: "localhost:9092"

data_lake:
  bronze_path: "data/lake/bronze"
  checkpoint_path: "data/checkpoints"
  dead_letter_path: "data/lake/dead_letter"

processing:
  watermarks:
    eeg_minutes: 10
    ehr_minutes: 30
  target_hours: 100.0
```

---

## Testing Strategy

### Unit Tests (47 passing, 8 skipped)

| Test File | Coverage | Status |
|-----------|----------|--------|
| `test_anomaly_rules.py` | Score calculation, classification | 10/10 pass |
| `test_dead_letter.py` | DLQ write/read, count | 3/3 pass |
| `test_kafka_helpers.py` | Serialization, FileProducer | 3/3 pass |
| `test_bronze_writer.py` | Dedup, validation, DLQ routing | 4/4 pass |
| `test_eeg_producer.py` | Manifest parsing, publish | 3/3 pass |
| `test_ehr_normalizer.py` | EHR generation, normalization | 5/5 pass |
| `test_download_eeg_ehr.py` | Manifest building, credentials | 4/4 pass |

### Test Without Kafka/Spark

All tests pass without Docker or optional dependencies:

```bash
# Core tests (no Kafka/Spark)
pytest tests/test_kafka_helpers.py tests/test_bronze_writer.py -v

# Spark-dependent tests (skipped if pyspark not installed)
pytest tests/test_bronze_ingest.py tests/test_speed_layer.py -v
# 8 tests skipped (require Spark)
```

### End-to-End Demo

```bash
# 1. Generate manifest
python scripts/download_eeg_ehr.py \
    --csv-dir ../STELAR-private/pretrain/reve/metadata \
    --output artifacts/week2/download_manifest.json \
    --target-hours 100 --dry-run

# 2. Replay to Kafka (file fallback)
python scripts/replay_to_kafka.py \
    --manifest artifacts/week2/download_manifest.json \
    --fallback

# 3. Verify output
head artifacts/week2/kafka_fallback.jsonl
```

---

## Team Roles

### Quang-Hung (Lead / Architect)
- Spark pipeline orchestration
- Code review for all PRs
- Speed layer implementation
- Integration testing

### Kim-Hung (Engineer)
- Kafka connection helpers
- Bronze writer with dedup
- Cassandra sink
- S3 download loop

### Kim-Quan (Engineer)
- EEG producer
- EHR normalizer + synthetic generation
- Anomaly scoring rules
- Alert publisher

### Dat (Engineer)
- Dead-letter queue
- Docker Compose stack (Kafka KRaft, Spark)
- Kubernetes manifests

### Trang (Engineer)
- Download manifest CLI
- All unit tests
- End-to-end demo integration

---

## Appendix: Common Commands

### Local Development

```bash
# Start Kafka + Spark stack
docker compose -f infra/docker/docker-compose.yml up -d

# Run tests
pytest -v

# Generate demo data
python scripts/download_eeg_ehr.py \
    --csv-dir ../STELAR-private/pretrain/reve/metadata \
    --output artifacts/week2/download_manifest.json \
    --target-hours 10

# Replay events
python scripts/replay_to_kafka.py \
    --manifest artifacts/week2/download_manifest.json \
    --fallback
```

### Production Deployment

```bash
# Deploy to Kubernetes
./infra/k8s/deploy.sh

# Check status
kubectl get pods -n brainwatch

# View logs
kubectl logs -l app=spark-streaming -f
```

---

*Document version: 1.0*  
*Last updated: 2026-05-19*  
*Author: BrainWatch Team*
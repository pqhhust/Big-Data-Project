# BrainWatch — Environment Setup Guide

## 1. Python Environment (uffm)

The project borrows the existing `uffm` conda environment (Python 3.11).

```bash
# Activate
conda activate uffm

# Install brainwatch in editable mode
cd /mnt/disk1/aiotlab/pqhung/ipp-proposal/Big-Data-Project
pip install -e ".[dev]"

# Optional: Kafka client (only needed when publishing to real Kafka)
pip install -e ".[dev,kafka]"

# Optional: PySpark (only needed for Spark jobs)
pip install -e ".[dev,spark]"

# Verify
python -c "from brainwatch.contracts.events import EEGChunkEvent; print('OK')"
pytest
```

## 2. Generate Download Manifest

No AWS credentials needed — this only reads local metadata CSVs.

```bash
python scripts/download_bdsp_subset.py \
    --csv-dir ../STELAR-private/pretrain/reve/metadata \
    --output artifacts/week2/download_manifest.json \
    --target-hours 100 \
    --min-duration 60 \
    --max-duration 1200
```

Output: `artifacts/week2/download_manifest.json` with ~1,400 subjects from 5 sites.

### Download EDF files (requires AWS credentials)

```bash
# Set up BDSP credentials first
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...

# Dry run (show what would be downloaded)
python scripts/download_bdsp_subset.py \
    --csv-dir ../STELAR-private/pretrain/reve/metadata \
    --output artifacts/week2/download_manifest.json \
    --download --dry-run

# Actual download
python scripts/download_bdsp_subset.py \
    --csv-dir ../STELAR-private/pretrain/reve/metadata \
    --output artifacts/week2/download_manifest.json \
    --download --download-root data/raw/eeg
```

## 3. Replay Events (No Kafka Required)

Test the full ingestion pipeline with file-based fallback:

```bash
python scripts/replay_to_kafka.py \
    --manifest artifacts/week2/download_manifest.json \
    --fallback
```

Output: `artifacts/week2/kafka_fallback.jsonl` with all EEG + synthetic EHR events.

## 4. Local Kafka Stack (Docker)

```bash
# Start Kafka + Zookeeper + Spark + Kafka UI
docker compose -f infra/docker/docker-compose.yml up -d

# Verify topics were created
docker exec -it $(docker ps -qf name=kafka-1) \
    kafka-topics.sh --bootstrap-server kafka:9092 --list

# Kafka UI: http://localhost:8080
# Spark UI: http://localhost:8081
```

### Replay to real Kafka

```bash
pip install -e ".[kafka]"

python scripts/replay_to_kafka.py \
    --manifest artifacts/week2/download_manifest.json \
    --bootstrap-servers localhost:9094
```

(Use port 9094 for external access from host machine.)

## 5. Kubernetes Deployment

```bash
# Create namespace and config
kubectl apply -f infra/k8s/namespace.yaml
kubectl apply -f infra/k8s/configmap.yaml

# Deploy Zookeeper → Kafka → Spark job
kubectl apply -f infra/k8s/zookeeper-deployment.yaml
kubectl apply -f infra/k8s/kafka-statefulset.yaml
kubectl apply -f infra/k8s/spark-streaming-job.yaml
```

## 6. Run Tests

```bash
conda activate uffm
pytest -v
```

All 24 tests should pass without Docker or Kafka running.

## 7. Project Structure (Week 2)

```
Big-Data-Project/
├── src/brainwatch/
│   ├── contracts/events.py          # Canonical event schemas
│   ├── ingestion/
│   │   ├── eeg_inventory.py         # Metadata profiling (Week 1)
│   │   ├── subset_manifest.py       # Subset selection (Week 1)
│   │   ├── kafka_helpers.py         # Kafka utilities + FileProducer fallback
│   │   ├── eeg_producer.py          # EEG → Kafka publisher
│   │   ├── ehr_normalizer.py        # Synthetic EHR + normalisation
│   │   ├── bronze_writer.py         # Bronze zone writer with dedup
│   │   └── dead_letter.py           # Dead letter queue
│   ├── processing/
│   │   ├── spark_pipeline.py        # Streaming skeleton (Week 1)
│   │   └── bronze_ingest.py         # Kafka → Bronze Structured Streaming
│   └── serving/anomaly_rules.py     # Alert classification
├── scripts/
│   ├── download_bdsp_subset.py      # ~100h download manifest builder
│   ├── replay_to_kafka.py           # Unified EEG + EHR replay
│   ├── profile_eeg_metadata.py      # Metadata profiling (Week 1)
│   └── build_local_subset_manifest.py
├── infra/
│   ├── docker/docker-compose.yml    # Local Kafka + Spark stack
│   └── k8s/                         # Kubernetes manifests
├── tests/                           # 24 tests (all pass)
├── artifacts/
│   ├── week1/                       # Metadata profile + subset manifest
│   └── week2/                       # Download manifest + event replay
└── docs/
    ├── architecture.md
    ├── week1-checklist.md
    ├── week2-checklist.md
    └── setup-guide.md
```

# BrainWatch

BrainWatch is a Lambda-architecture data platform for hospital-scale EEG
monitoring. It ingests real EDF recordings and EHR-like clinical events,
maintains a bronze/silver/gold lake on HDFS, scores near-real-time anomalies
with Spark Structured Streaming, stores alerts in Cassandra, and serves
operational dashboards through Grafana.

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![Spark 3.5](https://img.shields.io/badge/spark-3.5-orange)](https://spark.apache.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

## What It Runs

The deployed system is designed around two paths over the same clinical data:

```text
BDSP EDF archive + HEEDB clinical metadata
    -> bronze JSONL
    -> HDFS lake
    -> Spark batch silver/gold tables
    -> patient features and dashboard samples

bronze EEG/EHR events
    -> Kafka topics
    -> Spark Structured Streaming
    -> Cassandra alerts
    -> S3 rollup JSON
    -> Grafana dashboards

raw EDF in HDFS
    -> WebHDFS EEG signal exporter
    -> compact waveform JSON in S3
    -> Grafana EEG Signal Viewer
```

Core runtime components:

| Layer | Technology | Role |
|---|---|---|
| Ingestion | Python, MNE, Kafka producers | EDF parsing, EHR event generation, replay into Kafka |
| Message bus | Kafka 3.9 KRaft | Replayable event log for EEG and EHR streams |
| Batch processing | Spark 3.5 | Bronze to silver/gold lake transforms |
| Stream processing | Spark Structured Streaming | Windowed anomaly scoring and Cassandra writes |
| Distributed storage | HDFS, RF=2 | Bronze, silver, gold, raw EDF copy, Spark checkpoints |
| Object storage | S3 | Raw archive, dashboard JSON, code bundle for pods |
| Serving store | Cassandra 4.1 | Alert and patient enrichment tables |
| Dashboards | Grafana 11, Infinity datasource, React/Vite | Live operational views and EEG waveform viewer |
| Orchestration | Kubernetes on AWS EKS | Stateful and stateless workloads |

## Repository Layout

```text
configs/                     Runtime configuration templates
dashboard/                   React dashboard frontend
infra/
  docker/                    Local Kafka/Spark compose stack
  k8s/                       Base Kubernetes manifests
  cloud/                     EKS manifests, Grafana dashboards, resume scripts
scripts/                     CLI entry points and cluster jobs
src/brainwatch/              Core Python package
  contracts/                 Event dataclasses
  ingestion/                 Bronze writer, Kafka helpers, dead-letter handling
  processing/                Spark silver/gold/speed-layer logic
  analytics/                 Rollups, ICD/HEEDB helpers
  serving/                   Cassandra sink and anomaly rules
tests/                       Pytest suite
```

`docs/` is intentionally local-only and ignored by Git. The committed
technical entry point is this README.

## Local Setup

```bash
cd /mnt/disk1/aiotlab/pqhung/courseworks/Big-Data-Project
source /mnt/disk1/aiotlab/envs/uffm/bin/activate
pip install -e ".[dev,spark,kafka]"
```

Optional local services:

```bash
docker compose -f infra/docker/docker-compose.yml up -d
```

Frontend:

```bash
cd dashboard
npm install
npm run dev
```

## Real Data Pipeline

The BDSP root key is expected outside the repository:

```bash
export BDSP_CREDENTIALS=/mnt/disk1/aiotlab/pqhung/courseworks/credentials/rootkey.csv
```

Download a bounded real EDF subset, convert it to bronze, build real EHR
events, then materialize silver/gold:

```bash
python scripts/download_real_edf.py \
  --target-gb 1 \
  --sites I0002 I0003 S0001 S0002 \
  --min-duration 120 \
  --max-duration 1800

python scripts/edf_to_bronze.py --bronze data/lake/bronze_real
python scripts/build_real_ehr.py --bronze data/lake/bronze_real

python scripts/run_batch.py \
  --bronze data/lake/bronze_real \
  --silver data/lake/silver_real \
  --gold data/lake/gold_real \
  --alerts-export artifacts/demo/alerts_real.jsonl
```

## EEG Signal Viewer

Grafana cannot render EDF files directly. BrainWatch keeps the EDF binaries in
HDFS and exports a compact, bounded JSON slice for visualization:

```bash
python scripts/export_eeg_signal_viewer.py \
  --source hdfs \
  --hdfs-root /lake/bronze/edf \
  --webhdfs-url http://hdfs-namenode-0.hdfs-namenode.brainwatch.svc.cluster.local:9870 \
  --output-dir dashboard/public/eeg_signals \
  --seconds 30 \
  --max-samples 1200 \
  --channels 19 \
  --prefer-subject I0002150000051
```

On EKS this is automated by
`infra/cloud/k8s-overlays/eeg-signal-exporter.yaml`. The exporter reads EDF
through WebHDFS, writes `index.json` and per-record waveform JSON, and uploads
the result to the dashboard S3 bucket under `eeg_signals/`.

## Cloud Deployment

Prerequisites: `aws`, `eksctl`, `kubectl`, AWS credentials for the project
account, and the snapshot inventory under `artifacts/eks/snapshots/`.

```bash
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_DEFAULT_REGION=us-east-1

bash infra/cloud/resume_from_snapshots.sh
kubectl -n brainwatch get pods
```

The resume script recreates the EKS cluster, restores EBS volumes from
snapshots, reapplies HDFS/Kafka/Cassandra/Grafana/Spark workloads, and
provisions the Grafana dashboards ConfigMap.

Current dashboard entry points for the running cluster:

```text
Grafana:              http://3.237.253.77:30300
Architecture Status: http://3.237.253.77:30300/d/brainwatch-arch/brainwatch-c2b7-architecture-status
EEG Signal Viewer:   http://3.237.253.77:30300/d/brainwatch-eeg-signal-viewer/brainwatch-c2b7-eeg-signal-viewer
```

The worker public IP can change after a cluster rebuild. Use
`kubectl -n brainwatch get svc grafana` and the worker node address if the URLs
stop resolving.

## Verification

Python and Spark-facing checks:

```bash
pytest -q
python -m py_compile scripts/export_eeg_signal_viewer.py
python -m json.tool infra/cloud/grafana-cluster-status-dashboard.json >/dev/null
python -m json.tool infra/cloud/grafana-eeg-signal-dashboard.json >/dev/null
bash -n infra/cloud/resume_from_snapshots.sh
```

Frontend build:

```bash
cd dashboard
npm run build
```

Cluster checks:

```bash
kubectl -n brainwatch get pods
kubectl -n brainwatch get cronjob
kubectl -n brainwatch exec sts/hdfs-namenode -- hdfs dfs -ls /lake/bronze/edf
kubectl -n brainwatch exec sts/cassandra -- cqlsh -e \
  "SELECT COUNT(*) FROM brainwatch.alerts;"
```

Dashboard JSON checks:

```bash
curl -s http://brainwatch-dashboard-923884399064.s3-website-us-east-1.amazonaws.com/cluster_summary.json | jq
curl -s http://brainwatch-dashboard-923884399064.s3-website-us-east-1.amazonaws.com/eeg_signals/index.json | jq
```

## Operational Notes

- HDFS is the compute-side filesystem for lake data and checkpoints.
- S3 is the serving-side store for Grafana JSON and survives EKS teardown.
- Cassandra alert writes are keyed by patient and alert time, so replayed
  micro-batches upsert the same logical alert row.
- The cloud deployment avoids custom image builds by copying scripts and wheels
  from S3 into init containers.
- Generated data, dashboard exports, credentials, and all local documentation
  are ignored by Git.

## License

MIT License. See [LICENSE](LICENSE).

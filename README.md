# BrainWatch — Big Data Platform for Real-Time EEG Monitoring

Lambda-architecture pipeline for hospital-scale EEG monitoring with EHR enrichment.
Stack: Kafka → Spark (batch + Structured Streaming) → Cassandra/MongoDB on Kubernetes.

> **Week 2 work breakdown is in [`docs/week2-work-packages.md`](docs/week2-work-packages.md).**
> Architecture: [`docs/architecture.md`](docs/architecture.md) · Lead: Quang-Hung.

---

## 1. Environment setup

We share the existing `uffm` conda env on the lab GPU server (Python 3.11).

```bash
# 1) Activate the shared env
conda activate uffm

# 2) Install the package in editable mode (pytest only — no Spark/Kafka)
cd /mnt/disk1/aiotlab/pqhung/ipp-proposal/Big-Data-Project
pip install -e ".[dev]"

# 3) Add Kafka/Spark when you actually need them (optional extras)
pip install -e ".[dev,kafka]"   # only if you publish to a real Kafka broker
pip install -e ".[dev,spark]"   # only if you run PySpark locally

# 4) Smoke check
python -c "from brainwatch.contracts.events import EEGChunkEvent; print('OK')"
pytest -v
```

All week-1 tests pass without Docker, Kafka, or Spark — that is intentional.

## 2. AWS credentials (for downloading BDSP EEG)

The download script reads AWS keys from a CSV file. The lab key lives at:

```
/mnt/disk1/aiotlab/pqhung/ipp-proposal/credentials/rootkey.csv
```

Format (standard AWS IAM root-key export):

```csv
Access key ID,Secret access key
<key>,<secret>
```

> **Never commit this file.** It sits outside the repo on purpose.
> If you copy it locally, add the path to your local `.gitignore`.

The download script picks the path up via `--credentials` or the `BDSP_CREDENTIALS`
env var; see [`scripts/download_eeg_ehr.py`](scripts/download_eeg_ehr.py).

## 3. Local Kafka stack (optional, only for stream tests)

```bash
docker compose -f infra/docker/docker-compose.yml up -d
# Kafka UI:   http://localhost:8080
# Spark UI:   http://localhost:8081
# Internal :  kafka:9092   |  External (host):  localhost:9094
```

## 4. Run a single test

```bash
pytest tests/test_eeg_inventory.py -v
pytest tests/test_eeg_inventory.py::test_profile_writes_summary -v
```

## 5. Branching

- `main` — protected, integration only. PR + review from Quang-Hung.
- `Hunng`, `Hùng`, `quankim`, `nguyendinhdat`, `trang` — per-member working branches.
- Open a PR into `main` once your work-package acceptance criteria pass.

## 6. Roadmap

| Week | Focus                                           | Status      |
| ---- | ----------------------------------------------- | ----------- |
| 1    | Foundation, architecture, project scaffold      | Complete    |
| 2    | EEG/EHR download + Kafka ingestion + bronze     | In progress |
| 3    | Batch layer — Bronze → Silver → Gold Spark jobs | Planned     |
| 4    | Speed layer — Structured Streaming & alerts     | Planned     |
| 5    | Serving layer, dashboards, hardening            | Planned     |
| 6    | End-to-end integration, report, demo            | Planned     |

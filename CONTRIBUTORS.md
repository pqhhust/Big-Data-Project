# Contributors & Role Attribution

BrainWatch is a team capstone for **IT4043E — Big Data Storage and Processing**
(HUST SOICT). Work is divided by layer ownership, mirrored in the git history.

| Member | Role | Owns (modules / files) |
|--------|------|------------------------|
| **Quang-Hung** (`pqhhust`) | Lead / Architect | Speed layer (`processing/speed_layer.py`), streaming integration (`run_speed_layer_kafka.py`), batch driver (`run_batch.py`), Kafka replay (`replay_to_kafka.py`), bronze ingest, overall architecture & code review |
| **Kim-Quan** (`quazkim`) | Batch-layer owner | Silver + Gold (`processing/silver_layer.py`, `gold_layer.py`), realtime replay (`eeg_replay.py`), clinical analytics + MLlib (`analytics/`, `train_severity_model.py`, `extract_clinical_insights.py`) |
| **Kim-Hung** (`hungkimyeu`) | Serving owner | Cassandra sink + alert publisher (`serving/cassandra_sink.py`, `alert_publisher.py`), anomaly rules v2 (`serving/anomaly_rules.py`), EDF Kafka producer + real-EDF ingest (`edf_kafka_producer.py`, `download_real_edf.py`, `edf_to_bronze.py`) |
| **Dat** (`Nguyễn Đình Đạt`) | Kubernetes / Deploy | All K8s manifests (`infra/k8s/`, `infra/cloud/k8s-overlays/`), deploy/teardown scripts, EKS cutover, resume-from-snapshots |
| **Trang** (`Truong Scarlett`) | Demo / Tests / EHR | EHR loader (`ingestion/ehr_loader.py`), end-to-end demo (`end_to_end_demo.py`), the React + Grafana dashboards, the test suite (`tests/`), demo data fixtures |

> Attribution note: the production codebase was assembled through paired and
> mob sessions; commit authorship reflects primary layer ownership rather than
> exclusive authorship. Module-level ownership above is the source of truth for
> "who can answer questions about X" during the defense.

## Data & credit
- EEG waveforms: **BDSP** (Brain Data Science Platform, Harvard/MGH) credentialed
  access point — used under the team's data-use credential for coursework only,
  not redistributed.
- ICD-10 diagnoses: **HEEDB** neurology table (BDSP).
- See `docs/PRESENTATION-GUIDE.md` for the full background + defense Q&A.

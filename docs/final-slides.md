# BrainWatch — Capstone Defence Slides

> 15-slide deck for the IT4043E milestone presentation. One H2 per slide; bullets
> are the talking points, not the literal text. Convert to `.pdf` via Marp or
> Pandoc + Beamer for submission.

---

## 1. Title

- **BrainWatch — Lambda-Architecture EEG Anomaly Detection**
- IT4043E · Spring 2026 · HUST SOICT
- Team: Quang-Hung, Kim-Hung, Kim-Quan, Dat, Trang
- Repo: `github.com/pqhhust/Big-Data-Project` · tag `v1.0.0`

---

## 2. The Problem in One Slide

- Hospital neuro-ICU: 50 beds × 19-channel 200 Hz EEG = ~4 TB / month
- Clinical context (vital signs, labs, meds) arrives asynchronously
- A nurse can't watch 50 EEG streams in real time
- → Need automatic severity-classified alerts, with EHR context

---

## 3. Why "Big Data"

- 5 Vs satisfied: Volume (TBs/month), Velocity (continuous chunks), Variety (binary EDF + JSON EHR), Veracity (dedup + versioning), Value (clinician time saved)
- Single-machine Spark hits memory walls past 8 GiB; we ship a path to horizontal scale

---

## 4. Lambda vs Kappa — Why Lambda

| | Lambda | Kappa |
|---|---|---|
| Historical backfill | Native | Replay everything |
| Independent debugging | 2 pipelines | 1 fragile pipeline |
| Team parallelism | Layer-based | Sequential |
- Lin (2017) — Kappa wins when state is bounded and replay is cheap
- Our state isn't bounded; replay is expensive → Lambda

---

## 5. System Architecture

(Insert the diagram from §2.2 of `final-report.md`)

- Ingestion → Kafka (`eeg.raw`, `ehr.updates`)
- Bronze: Spark Streaming → Parquet + JSONL, SHA-256 dedup, DLQ on bad rows
- Speed: stream-stream join, anomaly UDF, foreachBatch → Cassandra + Kafka
- Batch: CronJob → silver (dedup + latest-version) → gold (broadcast join + rollups)
- Serving: Cassandra alerts + Matplotlib dashboard

---

## 6. Technology Stack

| Layer | Choice | Why |
|---|---|---|
| Messaging | Apache Kafka 3.9 (KRaft locally / Zookeeper on K8s) | Industry default, partition by patient_id |
| Stream / Batch | Spark 3.5 Structured Streaming | One engine for both Lambda halves |
| Serving | Cassandra 4.1 | Wide-row, time-series clustering |
| Orchestration | Kubernetes (namespace `brainwatch`) | Stated rubric requirement |

---

## 7. Advanced Spark Features Used

- **Window funcs** — `row_number().over(partitionBy.orderBy(version DESC))` (silver EHR)
- **Broadcast join** — `F.broadcast(patient_dim)` (gold) — asserted in tests
- **Sort-merge join** — EEG ⋈ EHR on patient_id + ±30 min predicate (gold)
- **UDF** — `compute_anomaly_score` (speed layer)
- **Watermark + state** — 10 min EEG / 30 min EHR
- **Partition pruning** — `partitionBy(site_id, ingestion_date)` (silver)
- **`coalesce(4)`** — 64–256 MiB target files

---

## 8. Bronze → Silver → Gold

- **Bronze:** raw JSONL, `site=*/date=*`, SHA-256 dedup, DLQ
- **Silver:** dedup again on `(patient_id, session_id, event_time)`; EHR via window `row_number()`; `quality_flag ∈ {OK, LOW_SR, SHORT_WINDOW}`
- **Gold:** broadcast(patient_dim) ⋈ EEG ⋈ EHR(±30min) → per-patient daily rollups (`n_eeg_chunks`, `mean_sampling_rate`, `has_critical_lab_today`, `n_medication_changes`)

---

## 9. Speed Layer Demo

- Streaming Parquet sources over bronze (no re-parsing Kafka)
- Stream-stream left-outer join with watermarks
- 1-minute tumbling window, 30 s slide
- UDF score → 5-tier severity (`compute_anomaly_score`, `classify_v2`)
- `foreachBatch` → Cassandra alerts (durable) + Kafka `alerts.anomaly` (fan-out)

---

## 10. Kubernetes Deployment

- 9 manifests, 17 resources, validated offline with `kubeconform`
- `deploy.sh` order: namespace → configmap → PVCs → zookeeper → kafka → cassandra → spark-streaming → spark-batch CronJob
- Each layer awaited with `kubectl rollout status … --timeout=300s`
- `teardown.sh --delete-pvcs` double-prompts before destroying data

---

## 11. Testing — 67 tests, all passing

- Unit (no Spark): contracts, anomaly rules, DLQ, Kafka helpers, bronze writer, producers
- Integration (Spark `local[2]`): silver dedup, silver `row_number()`, silver quality flag, gold aggregations, gold broadcast plan
- Structural: speed-layer signature
- Mocked sinks: Cassandra `_FakeSession`, Kafka `FileProducer`

---

## 12. Demo at Scale

- `generate_demo_data_at_scale.py` produced ≥ **8 GiB** of synthetic bronze events
- Throughput ~45 k events / s on a single writer
- Silver run < 2 min, gold run < 1 min on the test fixtures
- See `artifacts/demo/figures/` for severity histogram, alert timeline, anomaly score distribution, top-5 patients

---

## 13. Lessons Learned — Top 3

1. **Bronze + silver double-dedup is necessary.** At-least-once Kafka + EHR version mutations are different failure modes.
2. **Pin Spark join plans with assertions.** `assert "BroadcastHashJoin" in df.queryExecution.toString()` prevented an SMJ regression.
3. **Banish `pass` test bodies.** We discovered late that some Spark tests were stubs; rewriting them to assert exact values closed a real coverage hole.

---

## 14. Limitations & Future Work

- Single-replica Kafka / Cassandra in the demo cluster (resource budget)
- Speed-layer scoring is rule-based; an MLlib classifier is the natural next step (`Lesson 8` foreshadows this)
- Dashboard is Matplotlib-static; the React/Vite scaffold in `dashboard/` is the next-sprint target
- No Prometheus exporter — `lastProgress` JSONL tail covers 80% of the value

---

## 15. Questions

- Repo: `github.com/pqhhust/Big-Data-Project`
- Tag: `v1.0.0`
- Demo video: `artifacts/demo/demo.mp4` (≤ 5 min)
- Report: `docs/final-report.md`

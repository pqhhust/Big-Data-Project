# Final Sprint Plan — 2 Weeks to Ship

**Reality check.** We have only 2 weeks left. We compress the original
6-week roadmap into one engineering week + one report week. This document
is the authoritative source — supersedes `week2-work-packages.md`.

| Phase | Dates                       | Goal                                                |
| ----- | --------------------------- | --------------------------------------------------- |
| 1     | **Fri 2026-05-08 → Thu 2026-05-14** | Code complete + deployed on Kubernetes      |
| 2     | **Fri 2026-05-15 → Thu 2026-05-21** | Final report, demo video, slide deck, defense |

**Code freeze: Thu 2026-05-14 21:00 ICT.**  After freeze, only doc PRs.

---

## What's already done (Hunng branch)

Quang-Hung's Week-1 + Week-2 implementation is on `Hunng`:
- Canonical event schemas, anomaly rules (Week 1)
- Download script (`download_bdsp_subset.py`), Kafka helpers + FileProducer,
  EEG/EHR producers, bronze writer + DLQ, Spark Kafka→Bronze streaming, replay
  simulator, Docker Compose stack, base K8s manifests (Week 2)
- 24 passing unit tests

**Day-1 action: Quang-Hung merges `Hunng` → `main` Friday morning.** Everyone
rebases their personal branch onto the new `main` before starting their package.

---

## What still needs to be built (this week)

| #   | Item                                               | Owner     |
| --- | -------------------------------------------------- | --------- |
| W3  | Bronze → Silver Spark batch                        | Kim-Hung  |
| W3  | Silver → Gold Spark batch                          | Kim-Hung  |
| W4  | Speed layer (stream-stream join + alerts)          | Quang-Hung|
| W4  | Anomaly rules v2 (more severities, per-feature)    | Kim-Quan  |
| W5  | Cassandra serving sink                             | Kim-Quan  |
| W5  | Alert publisher (Spark → Cassandra + Kafka topic)  | Kim-Quan  |
| W5  | K8s: Cassandra StatefulSet + PVCs                  | Dat       |
| W5  | K8s: Spark batch CronJob + streaming Deployment    | Dat       |
| W5  | K8s: `deploy.sh` / `teardown.sh`                   | Dat       |
| W6  | End-to-end demo orchestration script               | Trang     |
| W6  | Demo dashboard (matplotlib / Streamlit)            | Trang     |
| W6  | Tests for silver / gold / speed / serving          | Trang     |
| W6  | K8s production deploy + smoke test                 | Quang-Hung + Dat |

Each item has a placeholder file already on `main` after the merge.

---

## Sprint Week — per-member work packages

### Quang-Hung (lead) — 2026-05-08 → 2026-05-14

**Files you own**
- `src/brainwatch/processing/speed_layer.py`
- `infra/k8s/deploy.sh` (review with Dat) + final cluster cutover
- Code review: every PR from the team

**Tasks**
1. **Day 1 (Fri):** merge `Hunng` → `main` (squash or merge commit, your call).
   Tag `v0.2.0-week2-done`. Notify the team to rebase.
2. **Day 1–3:** speed layer:
   - Read `bronze/eeg/*` and `bronze/ehr/*` as **streaming** Parquet sources
     (not Kafka — bronze is already authoritative; this avoids re-parsing).
     Watermark EEG 10 min, EHR 30 min.
   - Stream-stream `leftOuter` join on `patient_id` with EHR within
     `event_time - 30 min ≤ ehr.event_time ≤ event_time`.
   - Stateful 1-minute tumbling window aggregations (chunk count, mean SR,
     `has_critical_lab` flag).
   - Anomaly score from Kim-Quan's rules v2 → write to Kafka `alerts.anomaly`
     and the `features.realtime` topic.
4. **Day 4–5:** integration. Run the full pipeline end-to-end against
   replayed data. Fix what breaks.
5. **Day 6 (Wed):** with Dat, deploy the full stack to K8s namespace
   `brainwatch`. Run Trang's E2E demo against the cluster.
6. **Day 7 (Thu):** code freeze. Tag `v0.3.0-rc1`. Collect screenshots for
   the report.

**Acceptance**
- `pytest -v` green.
- A 5-minute replay produces ≥ 1 critical alert visible in Cassandra.
- `kubectl -n brainwatch get pods` all `Running` before freeze.

---

### Kim-Hung — 2026-05-08 → 2026-05-14

**Files you own**
- `src/brainwatch/processing/silver_layer.py`
- `src/brainwatch/processing/gold_layer.py`

**Tasks**
1. **Silver layer (Day 1–3):**
   - `build_eeg_silver(spark, bronze_path, silver_path)` — read
     `bronze/eeg/*.parquet`, **deduplicate** on
     `(patient_id, session_id, event_time)`, drop rows with bad
     `sampling_rate_hz` (`<= 0` or `> 1000`), add `quality_flag` column
     (`OK` / `LOW_SR` / `SHORT_WINDOW`), write `silver/eeg/` partitioned by
     `site_id, ingestion_date`.
   - `build_ehr_silver(spark, bronze_path, silver_path)` — read
     `bronze/ehr/*.parquet`, resolve **latest version per (patient_id,
     encounter_id)** using Spark window function
     (`row_number() over partition by ... order by version desc`). Keep only
     `row_number = 1`. Write `silver/ehr/` partitioned by `ingestion_date`.
   - `build_patient_dim(spark, ...)` — small reference dim table joining
     unique patient ids from both streams.
2. **Gold layer (Day 3–5):**
   - `build_patient_features(spark, silver_path, gold_path)` — broadcast-join
     silver EEG with patient dim, join silver EHR within ±30 min, compute
     per-patient daily rollups: `n_eeg_chunks`, `mean_sampling_rate`,
     `has_critical_lab_today`, `n_medication_changes`. Write
     `gold/patient_features/` partitioned by `event_date`.
   - `build_alert_summary(spark, ...)` — read alerts from Cassandra (or
     `gold/alerts_export` if Kim-Quan's sink exposes a Parquet export),
     produce daily counts by severity. Used by Trang's dashboard.
3. **Optimization checklist (Day 5):**
   - `df.explain()` on every batch; flag any sort-merge join on a small
     table (broadcast it instead).
   - Coalesce small Parquet files at write time (target 64-256 MB per file).
   - Document one before/after `explain()` in `docs/spark-optimization.md`
     for the report.

**Acceptance**
- `pytest tests/test_silver_layer.py tests/test_gold_layer.py` green
  (Trang owns the tests).
- Running `python -m brainwatch.processing.silver_layer --bronze data/lake/bronze --silver data/lake/silver` on Trang's demo data produces silver Parquet
  with no duplicates.

---

### Kim-Quan — 2026-05-08 → 2026-05-14

**Files you own**
- `src/brainwatch/serving/cassandra_sink.py`
- `src/brainwatch/serving/alert_publisher.py`
- expand `src/brainwatch/serving/anomaly_rules.py` to v2

**Tasks**
1. **Anomaly rules v2 (Day 1):**
   - Add a `compute_anomaly_score(features: dict) -> float` that combines
     `eeg_chunk_count`, `signal_quality`, `has_critical_lab`,
     `n_medication_changes_24h` into a 0–1 score.
   - Add a 4-tier severity: `critical / warning / advisory / normal /
     suppressed`. Document the thresholds in a docstring table.
2. **Cassandra schema + sink (Day 2–4):**
   - Cassandra schema (write the CQL into the docstring of `cassandra_sink.py`):
     ```cql
     CREATE KEYSPACE brainwatch ...;
     CREATE TABLE alerts (
         patient_id text, alert_time timestamp, severity text,
         anomaly_score float, explanation text, session_id text,
         PRIMARY KEY (patient_id, alert_time)
     ) WITH CLUSTERING ORDER BY (alert_time DESC);
     CREATE TABLE patient_state (
         patient_id text PRIMARY KEY,
         last_alert_time timestamp, last_severity text,
         signal_quality_score float, anomaly_score float
     );
     ```
   - `init_keyspace(session)` — apply the schema if missing (idempotent).
   - `write_alerts(session, alerts: list[AlertEvent])` — batch insert.
   - `upsert_patient_state(session, ...)`.
   - `query_recent_alerts(session, patient_id, limit=10)` for the dashboard.
3. **Alert publisher (Day 4–5):**
   - `publish_alerts(spark_df, cassandra_session, kafka_producer)` —
     `foreachBatch` style sink that writes the same row to **both** Cassandra
     (durable serving) and the `alerts.anomaly` Kafka topic (downstream
     consumers, dashboards). Idempotent on `(patient_id, alert_time)`.
4. **Integration with Quang-Hung (Day 5):** wire your sink into his speed
   layer's `writeStream.foreachBatch(publish_alerts)`.

**Acceptance**
- `pytest tests/test_serving.py` green (Trang).
- Manual demo: starting Cassandra in Docker, running `init_keyspace` then
  `write_alerts([fake_alert])`, then `query_recent_alerts` returns it.
- Severity thresholds documented in the rules-v2 docstring.

---

### Dat — 2026-05-08 → 2026-05-14

**Files you own**
- `infra/k8s/cassandra-statefulset.yaml`
- `infra/k8s/persistent-volumes.yaml`
- `infra/k8s/spark-batch-cronjob.yaml`
- `infra/k8s/spark-streaming-deployment.yaml` (replaces the bootstrap Job)
- `infra/k8s/deploy.sh`
- `infra/k8s/teardown.sh`

**Tasks**
1. **Persistent volumes (Day 1):**
   - One PVC for bronze, one for silver, one for gold, one for checkpoints,
     one for Cassandra data. `ReadWriteOnce` is fine — single-node cluster.
   - Storage class: whatever the lab cluster has (`standard` is the usual
     default; check with `kubectl get storageclass`).
2. **Cassandra StatefulSet (Day 1–2):**
   - Image `cassandra:4.1`, 1 replica is enough for the demo.
   - Headless service `cassandra-svc` for stable DNS.
   - Volume claim template referencing the storage class.
   - Liveness probe: `nodetool status`.
3. **Spark workloads (Day 2–4):**
   - **Streaming** (`spark-streaming-deployment.yaml`): replace today's
     bootstrap Job with a real `Deployment`, `restartPolicy: Always`, `image:
     bitnami/spark:3.5`, command `spark-submit ... brainwatch.processing.speed_layer`.
     Mount the configmap as env vars.
   - **Batch** (`spark-batch-cronjob.yaml`): `CronJob` running daily at
     03:00 UTC, runs Kim-Hung's silver and gold builders.
4. **Deploy script (Day 4–5):**
   - `deploy.sh` applies in order: namespace → configmap → secrets → PVCs
     → cassandra → kafka → zookeeper (if not KRaft) → spark workloads.
     Wait for each StatefulSet to be ready before moving on
     (`kubectl rollout status`).
   - `teardown.sh` reverse order. Always confirm before deleting PVCs.
5. **Day 6:** pair with Quang-Hung, deploy the whole thing, smoke test.

**Acceptance**
- `bash infra/k8s/deploy.sh` brings every pod to `Running`.
- `kubectl -n brainwatch exec -it cassandra-0 -- cqlsh -e "DESC KEYSPACES;"`
  shows the `brainwatch` keyspace after Kim-Quan's `init_keyspace` runs.
- `kubectl -n brainwatch logs deployment/spark-streaming` shows it consuming
  from Kafka.

---

### Trang — 2026-05-08 → 2026-05-14

**Files you own**
- `scripts/end_to_end_demo.py`
- `scripts/demo_dashboard.py`
- `tests/test_silver_layer.py`, `tests/test_gold_layer.py`,
  `tests/test_speed_layer.py`, `tests/test_serving.py`
- Demo data fixtures under `artifacts/demo/`

**Tasks**
1. **Demo data fixtures (Day 1):**
   - Generate a 50-subject mini-manifest (subset of Quang-Hung's 1,400-subject
     download manifest). Keep total < 200 MB so it fits in CI.
   - Pre-build a tiny synthetic EHR file (`artifacts/demo/synthetic_ehr.jsonl`)
     using Kim-Quan's generator at `events_per_subject=3`.
2. **End-to-end demo (Day 2–4):**
   - `scripts/end_to_end_demo.py` orchestrates:
     1. Replay the mini-manifest into Kafka.
     2. Wait for bronze parquet to land (poll the directory).
     3. Trigger silver + gold batch jobs (or wait for the streaming
        equivalents — your call).
     4. Query Cassandra for alerts; assert at least N critical alerts.
     5. Print a one-page summary to stdout.
   - Must work both **locally** (Docker Compose) and **on Kubernetes**
     (`--mode k8s` reads kafka host from the configmap).
3. **Dashboard (Day 4–5):**
   - Either matplotlib (one PNG per run, saved to `artifacts/demo/`) or a
     Streamlit single-page app with: live alert count, severity histogram,
     last 10 alerts table, signal-quality distribution. Pick whichever you
     can finish — matplotlib is the safer bet given the time budget.
4. **Tests (Day 1–5, alongside the team):**
   - As Kim-Hung lands silver/gold, write tests that load tiny fixtures and
     assert deduplication, version resolution, partition layout.
   - As Kim-Quan lands serving, mock the Cassandra session
     (in-memory dict) and test alert insert + query.
   - As Quang-Hung lands speed layer, write a structural test (skip if
     `pyspark` missing) that asserts the watermark + window are set.
5. **Day 6:** record a 3-minute screen-capture demo for the report.

**Acceptance**
- `pytest -v` green across the whole repo.
- `python scripts/end_to_end_demo.py --mode local` exits 0 and prints a
  summary with non-zero alert counts.
- One PNG dashboard screenshot committed to `artifacts/demo/`.

---

## Cadence (this week)

- **Daily standup, 19:00 ICT, 15 min on Discord.**
  Format: yesterday / today / blockers, two sentences each.
- **Wed 21:00 ICT — integration day.** All branches merged. Anything not
  in by then is dropped (we do NOT slip into report week).
- **Thu 21:00 ICT — code freeze.** Tag `v0.3.0-rc1`. Quang-Hung does the
  K8s production deploy.

## Definition of Done (sprint phase)

- [ ] `Hunng` merged into `main`.
- [ ] All scaffolded modules have real implementations (no `pass` left).
- [ ] `pytest -v` ≥ 35 passing tests.
- [ ] `python scripts/end_to_end_demo.py --mode local` exits 0.
- [ ] `bash infra/k8s/deploy.sh` brings the whole stack up on the lab cluster.
- [ ] `python scripts/end_to_end_demo.py --mode k8s` exits 0.
- [ ] One demo screenshot + one Cassandra query screenshot in `artifacts/demo/`.
- [ ] Tag `v0.3.0-rc1` pushed.

---

# Report Week — 2026-05-15 → 2026-05-21

No new code. We turn what we built into a paper, slides, and a demo video.

**Single source: `docs/final-report.md` + `docs/final-slides.md`.**
Quang-Hung is the editor — every member writes their section into the same
two files via PRs.

### Quang-Hung — editor + integration

- Architecture chapter (Lambda choice, system diagram, data flow).
- Combine everyone's sections into a coherent narrative.
- Final read-through Day 6.
- Demo video (≤ 5 min) cut from Trang's screen capture + voice-over.
- Slide deck final pass.
- Defence rehearsal Day 7.

**Deliverable:** `docs/final-report.md` (everyone's writing, your edit) +
`docs/final-slides.md` + `artifacts/demo/demo.mp4`.

### Kim-Hung — batch layer + performance section

- Write up Bronze → Silver → Gold (what each layer does, why).
- Spark optimization writeup: include the before/after `explain()` you
  documented during the sprint, plus 1–2 concrete numbers (rows/sec,
  shuffle MB).
- 1 figure: data lake zone diagram with row counts on a sample run.

**Deliverable:** sections "4. Batch Layer" and "7. Performance" in
`final-report.md`. ~2 pages.

### Kim-Quan — speed + serving section

- Speed layer description (stream-stream join, watermarks, why those
  numbers, late-data handling).
- Serving layer (Cassandra schema, query patterns, latency).
- Anomaly rules v2 — table of severity thresholds + rationale.
- 1 figure: alert flow EEG → Spark → Cassandra → dashboard.

**Deliverable:** sections "5. Speed Layer" and "6. Serving" in
`final-report.md`. ~2 pages.

### Dat — deployment + infra section

- K8s topology diagram (namespaces, services, persistent volumes).
- Deployment runbook (`deploy.sh` order, common failure modes).
- Resource budget table (CPU/memory request per pod).
- 1 figure: K8s topology.

**Deliverable:** section "8. Deployment" in `final-report.md`. ~1 page.

### Trang — testing, results, demo appendix

- Test strategy section (unit vs integration vs E2E, what we tested,
  coverage gaps).
- Results section: numbers from the E2E demo run (events processed,
  alerts generated, end-to-end latency).
- Screenshots: dashboard, K8s pod list, Cassandra query, Kafka UI.
- 3-minute demo video (raw screen capture; Quang-Hung edits voice-over).

**Deliverable:** sections "9. Testing", "10. Results", and "Appendix —
Demo" in `final-report.md`. ~2 pages + screenshots.

## Cadence (report week)

- Mon 21:00: section drafts due (each member opens a PR).
- Wed 21:00: Quang-Hung's first integrated read-through.
- Fri 21:00: figures finalised, screenshots in.
- Sun 21:00: defence rehearsal on Discord screen-share.
- **Mon 2026-05-22 morning: submission.**

## Definition of Done (project)

- [ ] `docs/final-report.md` complete and edited.
- [ ] `docs/final-slides.md` complete (or `.pdf` exported).
- [ ] `artifacts/demo/demo.mp4` ≤ 5 minutes.
- [ ] All authors listed; each section attributed.
- [ ] Repo tagged `v1.0.0` and pushed.

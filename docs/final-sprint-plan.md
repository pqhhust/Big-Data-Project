# Final Sprint Plan — 2 Weeks to Ship (rev 2)

**Reality check.** 2 weeks left. We compress weeks 3-6 into one engineering
week, then write the report in week 2. This document is the single source
of truth for both phases.

| Phase  | Dates                            | Goal                                                  |
| ------ | -------------------------------- | ----------------------------------------------------- |
| Sprint | **Fri 2026-05-08 → Thu 2026-05-14** | Code complete + deployed on Kubernetes. Tag `v0.3.0-rc1` |
| Report | **Fri 2026-05-15 → Thu 2026-05-21** | Final report, slides, ≤ 5-min demo video. Tag `v1.0.0`  |

**Code freeze: Thu 2026-05-14 21:00 ICT.** Submission: Mon 2026-05-22.

---

## 0. What each member has already shipped

Re-assignment below is grounded in observed work on each member's branch
(checked 2026-05-08).

| Member         | Branch              | Lines  | What                                                         | Style notes                                                                                  |
| -------------- | ------------------- | ------ | ------------------------------------------------------------ | -------------------------------------------------------------------------------------------- |
| **Quang-Hung** | `Hunng`             | ~21k   | Full week-2 ingestion stack (canonical paths, 24 tests)       | Lead's working baseline — gets merged to `main` Day 1.                                       |
| **Kim-Quan**   | `quankim`           | 316    | `eeg_replay.py` + tests + replay script                      | Cleanest architecture: frozen `@dataclass(slots=True)`, type hints, fits repo conventions.   |
| **Kim-Hung**   | `Hùng`              | 349    | `eeg_producer.py` (mne EDF reader, Kafka producer) + docker-compose | Deep Kafka skill (acks=all, retries=3, max_in_flight=1, request timeouts). Files at root — needs to be re-pathed. Vietnamese-mixed comments. |
| **Trang**      | `trang`             | 295    | `ehr_loader.py` (CSV → `EHREvent`) + tests                   | Real implementation, type hints, has honest `TODO` markers. Column names are PascalCase — will normalise to snake_case during merge. |
| **Dat**        | `nguyendinhdat`     | 1060   | `CODE_PLAN.md` only — **no code yet**                         | Strong planner/architect; suited to YAML-heavy K8s manifests + report sections, not the trickiest code.|

**Re-assignment rationale.** Kim-Quan ships the cleanest code that already
fits the codebase, so he is the strongest senior after Quang-Hung — promoted
to **batch layer owner** (silver + gold). Kim-Hung knows distributed clients
deeply (proven by his Kafka producer config); the Cassandra Python driver
follows the same pattern, so he gets **serving + alerts**. Dat has not yet
shipped code; K8s manifests are mostly YAML config and fit his
planning/documentation strength, with Quang-Hung pairing on the cluster
cutover. Trang ships real modules with tests, so she gets the **EHR ingestion
path + E2E demo + tests + dashboard** — more responsibility than the original
"tests-only" plan.

---

## 1. Merge strategy — Day 1 (Fri 2026-05-08)

| Branch          | Action                                                                           | Owner       |
| --------------- | -------------------------------------------------------------------------------- | ----------- |
| `Hunng`         | Squash-merge into `main` as the week-1 + week-2 canonical baseline. Tag `v0.2.0`. | Quang-Hung  |
| `quankim`       | Rebase onto new `main`. Keep `eeg_replay.py` as the **realtime replay** module (complements Hunng's batch publish). Open PR. | Kim-Quan    |
| `Hùng`          | Move `eeg_producer.py` → `src/brainwatch/ingestion/edf_kafka_producer.py`. Reconcile with Hunng's `eeg_producer.py` (manifest-based) — Hùng's version handles real EDF files; both can coexist. Open PR. | Kim-Hung    |
| `trang`         | Rebase onto new `main`. Normalise CSV column names to snake_case (`PatientID` → `patient_id`, etc.) to match the `EHREvent` contract. Open PR. | Trang       |
| `nguyendinhdat` | Salvage diagrams + section structure from `CODE_PLAN.md` for the final report. The branch itself is closed. | Dat         |

> Pairing rule: every PR needs one reviewer from {Quang-Hung, Kim-Quan, Kim-Hung}.

---

## 2. Sprint week — per-member work packages

### Quang-Hung (lead) — Fri → Thu

**Files you own**
- `src/brainwatch/processing/speed_layer.py`
- `infra/k8s/deploy.sh` (review with Dat) + final cluster cutover
- Code review: every PR from the team

**Tasks**
1. **Day 1 (Fri 2026-05-08).** Squash-merge `Hunng` → `main`. Tag `v0.2.0`.
   Notify the team to rebase.
2. **Day 1–3.** Speed layer:
   - Read `bronze/eeg/*` and `bronze/ehr/*` as **streaming** Parquet sources
     (not Kafka — bronze is already authoritative; this avoids re-parsing).
     Watermark EEG 10 min, EHR 30 min.
   - Stream-stream `leftOuter` join on `patient_id` with EHR within
     `event_time - 30 min ≤ ehr.event_time ≤ event_time`.
   - 1-min tumbling, 30s slide windowed aggregations (chunk count, mean SR,
     `has_critical_lab`).
   - Score with Kim-Hung's anomaly rules v2 → write to Kafka
     `alerts.anomaly` and `features.realtime` via Kim-Hung's `alert_publisher`.
3. **Day 4–5.** Integration. Run full pipeline against Trang's mini manifest.
   Fix what breaks.
4. **Day 6 (Wed).** With Dat, deploy the full stack to K8s namespace
   `brainwatch`. Run Trang's E2E demo against the cluster.
5. **Day 7 (Thu).** Code freeze. Tag `v0.3.0-rc1`. Collect screenshots.

**Acceptance**
- `pytest -v` green.
- 5-minute replay produces ≥ 1 critical alert in Cassandra.
- `kubectl -n brainwatch get pods` all `Running` before freeze.

---

### Kim-Quan (senior, batch layer owner) — Fri → Thu

**Files you own**
- `src/brainwatch/processing/silver_layer.py`
- `src/brainwatch/processing/gold_layer.py`
- Reconcile your existing `eeg_replay.py` from `quankim` with Hunng's
  `eeg_producer.py` (yours = realtime simulator, his = manifest publisher;
  both stay)

**Tasks**
1. **Silver (Day 1–3).**
   - `build_eeg_silver(spark, bronze_path, silver_path)` — read
     `bronze/eeg/*.parquet`, **deduplicate** on
     `(patient_id, session_id, event_time)`, drop bad rows
     (`sampling_rate_hz <= 0 or > 1000`), add `quality_flag` column
     (`OK / LOW_SR / SHORT_WINDOW`), write `silver/eeg/` partitioned by
     `site_id, ingestion_date`.
   - `build_ehr_silver(spark, bronze_path, silver_path)` — resolve **latest
     version per `(patient_id, encounter_id)`** with
     `row_number() over partition by ... order by version desc`. Keep `rn=1`.
     Write `silver/ehr/` partitioned by `ingestion_date`.
   - `build_patient_dim(...)` — broadcast-sized dim table for the gold join.
2. **Gold (Day 3–5).**
   - `build_patient_features(spark, silver_path, gold_path)` — broadcast-join
     EEG with patient dim, left-join EHR within ±30 min, daily rollups:
     `n_eeg_chunks, mean_sampling_rate, has_critical_lab_today,
     n_medication_changes`.
   - `build_alert_summary(spark, ...)` — daily counts by severity (read from
     Kim-Hung's Cassandra export).
3. **Optimisation (Day 5).** `df.explain()` on every batch — confirm
   `BroadcastHashJoin` for the patient dim. Coalesce small Parquet to
   64–256 MB. Document one before/after `explain()` for the report.
4. **Day 6.** Help Dat author the Spark batch CronJob (you know what
   `spark-submit` arguments your code needs).

**Acceptance**
- `pytest tests/test_silver_layer.py tests/test_gold_layer.py` green
  (Trang owns the tests).
- `python -m brainwatch.processing.silver_layer --bronze … --silver …`
  on Trang's demo data produces silver Parquet with no duplicates.
- `df.explain()` shows BroadcastHashJoin, not SortMergeJoin, for the
  patient dim.

---

### Kim-Hung (senior, serving owner) — Fri → Thu

**Files you own**
- `src/brainwatch/serving/cassandra_sink.py`
- `src/brainwatch/serving/alert_publisher.py`
- expand `src/brainwatch/serving/anomaly_rules.py` to v2
- merge your `Hùng` branch's `eeg_producer.py` → `src/brainwatch/ingestion/edf_kafka_producer.py`

**Tasks**
1. **EDF producer reconcile (Day 1).** Move your existing branch code to
   `src/brainwatch/ingestion/edf_kafka_producer.py`. It complements (does
   not replace) Hunng's manifest-based publisher: yours reads real EDF files
   via `mne`, his reads manifests. Keep both.
2. **Anomaly rules v2 (Day 1–2).**
   - `compute_anomaly_score(features: dict) -> float` blending
     `eeg_chunk_count`, `signal_quality`, `has_critical_lab`,
     `n_medication_changes_24h` into a 0–1 score. Document the formula.
   - 4-tier severity: `critical / warning / advisory / normal / suppressed`.
3. **Cassandra sink (Day 2–4).**
   - Schema (in the docstring of `cassandra_sink.py`): `alerts` table keyed
     `(patient_id, alert_time DESC)`; `patient_state` keyed `patient_id`.
   - `init_keyspace`, `write_alerts` (BatchStatement), `upsert_patient_state`,
     `query_recent_alerts(patient_id, limit)`. The `acks=all` /
     `max_in_flight=1` reflexes you used for Kafka apply directly to the
     Cassandra driver.
4. **Alert publisher (Day 4–5).**
   - `publish_alerts(batch_df, batch_id, cassandra_session, kafka_producer)`
     — `foreachBatch` sink. Filter to `severity in {critical, warning,
     advisory}`, write to **both** Cassandra and the `alerts.anomaly`
     Kafka topic, idempotent on `(patient_id, alert_time)`.
5. **Integration (Day 5).** Wire your sink into Quang-Hung's
   `writeStream.foreachBatch(publish_alerts)`.

**Acceptance**
- `pytest tests/test_serving.py` green (Trang).
- Manual: starting Cassandra in Docker, `init_keyspace` + `write_alerts(...)`
  + `query_recent_alerts(...)` round-trips successfully.
- Severity thresholds documented in the rules-v2 docstring.

---

### Dat (junior, K8s + deploy) — Fri → Thu

**Files you own**
- `infra/k8s/cassandra-statefulset.yaml`
- `infra/k8s/persistent-volumes.yaml`
- `infra/k8s/spark-batch-cronjob.yaml`
- `infra/k8s/spark-streaming-deployment.yaml` (replaces today's bootstrap Job)
- `infra/k8s/deploy.sh` / `teardown.sh`

**Tasks**
1. **PVCs (Day 1).** 5 `PersistentVolumeClaim` resources:
   `bronze 20Gi, silver 20Gi, gold 10Gi, checkpoints 5Gi, cassandra 20Gi`.
   `ReadWriteOnce`, default storage class.
2. **Cassandra (Day 1–2).** Headless `cassandra-svc` service + 1-replica
   StatefulSet, `cassandra:4.1`, volume claim template, `nodetool status`
   liveness probe.
3. **Spark workloads (Day 2–4).**
   - **Streaming** Deployment: `bitnami/spark:3.5`, `spark-submit -m
     brainwatch.processing.speed_layer ...` with `--packages
     spark-sql-kafka-0-10_2.12:3.5.0,spark-cassandra-connector_2.12:3.5.0`.
     `restartPolicy: Always`.
   - **Batch** CronJob: schedule `0 3 * * *`, runs Kim-Quan's silver+gold
     builders (he'll hand you the exact `spark-submit` args on Day 2).
4. **Deploy script (Day 4–5).** `deploy.sh` applies in dependency order
   (namespace → configmap → PVCs → kafka → cassandra → spark) and
   `kubectl rollout status` after each. `teardown.sh` reverse order with
   double-prompt before deleting PVCs.
5. **Day 6.** Pair with Quang-Hung on the cluster cutover. Bring your
   `CODE_PLAN.md` topology diagrams — they're the basis for the report
   deployment chapter you'll write next week.

**Acceptance**
- `bash infra/k8s/deploy.sh` brings every pod to `Running` on the lab cluster.
- `kubectl exec cassandra-0 -- cqlsh -e "DESC KEYSPACES;"` shows `brainwatch`.
- `kubectl logs deployment/spark-streaming` shows it consuming from Kafka.

---

### Trang (junior, demo + tests + EHR ingestion) — Fri → Thu

**Files you own**
- `scripts/end_to_end_demo.py`
- `scripts/demo_dashboard.py`
- `tests/test_silver_layer.py`, `test_gold_layer.py`, `test_speed_layer.py`,
  `test_serving.py`
- Promote your `trang` branch's `ehr_loader.py` to
  `src/brainwatch/ingestion/ehr_loader.py` on `main` (real CSV-based EHR
  ingestion, complementing Kim-Quan's synthetic generator on Hunng).
- Demo data fixtures under `artifacts/demo/`

**Tasks**
1. **EHR loader merge (Day 1).** Rebase `trang` onto new `main`. Normalise
   your CSV columns: `PatientID → patient_id`, `EncounterID → encounter_id`,
   etc., so they match the `EHREvent` contract. Resolve your `TODO` on
   timestamp parsing (use `datetime.fromisoformat` and reformat to ISO-Z).
   Open PR.
2. **Demo data (Day 1).**
   - 50-subject mini-manifest (subset of Hunng's 1,400-subject manifest).
     Total < 200 MB.
   - Pre-built synthetic EHR JSONL via Kim-Hung's generator.
3. **End-to-end demo (Day 2–4).** `scripts/end_to_end_demo.py` orchestrates:
   replay → wait for bronze → trigger silver+gold → query Cassandra → assert
   ≥ N critical alerts → print summary. Two modes: `--mode local` and
   `--mode k8s`.
4. **Dashboard (Day 4–5).** Matplotlib (safer in time budget): severity
   histogram, alert timeline, score distribution, top-5-patients markdown
   table. Save PNGs to `artifacts/demo/figures/`.
5. **Tests (Day 1–5, alongside the team).** As Kim-Quan lands silver/gold,
   Kim-Hung lands serving, Quang-Hung lands speed, write tests that load
   tiny fixtures and assert behaviour. Mock Cassandra + Kafka with the
   `_FakeSession` / `_FakeProducer` stubs already scaffolded.
6. **Day 6.** Record a 3-minute screen-capture demo for the report.

**Acceptance**
- `pytest -v` green across the repo (target: ≥ 35 tests).
- `python scripts/end_to_end_demo.py --mode local` exits 0 with non-zero
  alert count.
- One PNG dashboard screenshot committed to `artifacts/demo/figures/`.

---

## 3. Cadence (sprint week)

- **Daily standup, 19:00 ICT, 15 min on Discord.** Yesterday / today /
  blockers, two sentences each.
- **Wed 21:00 — integration day.** All branches merged. Anything not in by
  then is dropped — we do NOT slip into report week.
- **Thu 21:00 — code freeze.** Tag `v0.3.0-rc1`. Quang-Hung does the K8s
  production deploy.

## 4. Definition of Done (sprint)

- [ ] `Hunng` merged into `main`; `quankim`, `Hùng`, `trang` rebased and merged.
- [ ] All scaffolded modules have real implementations (no `pass` left).
- [ ] `pytest -v` ≥ 35 passing tests.
- [ ] `python scripts/end_to_end_demo.py --mode local` exits 0.
- [ ] `bash infra/k8s/deploy.sh` brings the whole stack up on the lab cluster.
- [ ] `python scripts/end_to_end_demo.py --mode k8s` exits 0.
- [ ] One demo screenshot + one Cassandra query screenshot in `artifacts/demo/`.
- [ ] Tag `v0.3.0-rc1` pushed.

---

# Report week — 2026-05-15 → 2026-05-21

No new code. We turn what we built into a paper, slides, and a demo video.
**Single source: `docs/final-report.md` + `docs/final-slides.md`.**
Quang-Hung is the editor — every member writes their section into the same
two files via PRs.

### Quang-Hung — editor + integration

- Architecture chapter (Lambda choice, system diagram, data flow). ~2 pages.
- Combine everyone's sections into a coherent narrative; final read-through
  Day 6.
- Demo video (≤ 5 min) cut from Trang's screen capture + voice-over.
- Slide deck final pass.
- Defence rehearsal Day 7.

**Deliverable:** `docs/final-report.md` (everyone's writing, your edit) +
`docs/final-slides.md` + `artifacts/demo/demo.mp4`.

### Kim-Quan — batch layer + performance section

- Bronze → Silver → Gold writeup (what each layer does, why those
  transformations, how the dedup + version-resolution works).
- Spark optimisation: include the before/after `df.explain()` you
  documented during the sprint, plus 1–2 numbers (rows/sec, shuffle MB).
- 1 figure: data lake zone diagram with row counts on a sample run.

**Deliverable:** sections "4. Batch Layer" and "7. Performance" in
`final-report.md`. ~2 pages.

### Kim-Hung — speed + serving section

- Speed layer (stream-stream join, watermarks, late-data handling — even
  though Quang-Hung wrote the code, you describe its serving contract).
- Serving (Cassandra schema, query patterns, write/read latency you
  measured).
- Anomaly rules v2 — table of severity thresholds + rationale.
- 1 figure: alert flow EEG → Spark → Cassandra → dashboard.

**Deliverable:** sections "5. Speed Layer" and "6. Serving" in
`final-report.md`. ~2 pages.

### Dat — deployment + infra section

- K8s topology diagram (namespaces, services, persistent volumes) — reuse
  what you drew in `CODE_PLAN.md`.
- Deployment runbook (the `deploy.sh` order, common failure modes you hit).
- Resource budget table (CPU/memory request per pod).
- 1 figure: K8s topology.

**Deliverable:** section "8. Deployment" in `final-report.md`. ~1 page.

### Trang — testing, results, demo appendix

- Test strategy (unit vs integration vs E2E, what we tested, coverage gaps).
- Results: numbers from the E2E demo run (events processed, alerts
  generated, end-to-end latency).
- Screenshots: dashboard, K8s pod list, Cassandra query, Kafka UI.
- 3-minute demo video (raw screen capture; Quang-Hung adds voice-over).

**Deliverable:** sections "9. Testing", "10. Results", and "Appendix —
Demo" in `final-report.md`. ~2 pages + screenshots.

## 5. Cadence (report week)

- Mon 21:00: section drafts due (each member opens a PR).
- Wed 21:00: Quang-Hung's first integrated read-through.
- Fri 21:00: figures finalised, screenshots in.
- Sun 21:00: defence rehearsal on Discord screen-share.
- **Mon 2026-05-22 morning: submission.**

## 6. Definition of Done (project)

- [ ] `docs/final-report.md` complete and edited.
- [ ] `docs/final-slides.md` complete (or `.pdf` exported).
- [ ] `artifacts/demo/demo.mp4` ≤ 5 minutes.
- [ ] All authors listed; each section attributed.
- [ ] Repo tagged `v1.0.0` and pushed.

# Week 2 — Work Packages

**Goal:** stand up the EEG + EHR ingestion layer end-to-end. By the end of Week 2 we
must be able to (a) download a real BDSP subset from S3, (b) replay both EEG and
synthetic EHR events through Kafka, and (c) land validated records in the
bronze zone of the data lake with deduplication and a dead-letter queue.

> Source of truth for "done" = green `pytest -v` + a successful end-to-end run of
> `scripts/download_eeg_ehr.py` followed by `scripts/replay_to_kafka.py`.

> ⚠️ **Stub warning.** Every function/test on `main` right now has a `pass`
> body — meaning `pytest` reports green even though nothing is actually
> implemented. Before you merge your work-package PR, every test you own must
> contain real assertions; remove the `pass` line and write the body.

---

## Team & roles

| Member         | Role                       | Strength                                             |
| -------------- | -------------------------- | ---------------------------------------------------- |
| **Quang-Hung** | Lead / architect           | Owns integration, Spark, code review                 |
| **Kim-Hung**   | Senior engineer            | Kafka + bronze writer (most plumbing-heavy modules)  |
| **Kim-Quan**   | Senior engineer            | Producers + EHR domain modelling                     |
| **Dat**        | Engineer                   | DLQ + local Docker stack                             |
| **Trang**      | Engineer                   | Download script CLI + tests                          |

Pairing rule: every PR needs one reviewer from {Quang-Hung, Kim-Hung, Kim-Quan}.

---

## Work package 1 — Quang-Hung (lead)

**Files you own**
- `src/brainwatch/processing/bronze_ingest.py` — Kafka → Bronze Structured Streaming
- `scripts/replay_to_kafka.py` — unified EEG + EHR replay simulator
- Integration / code review across all PRs

**Deliverables**
1. PySpark Structured Streaming job that subscribes to `eeg.raw` and `ehr.updates`,
   parses JSON against the canonical schemas in `contracts/events.py`, watermarks
   (10 min EEG / 30 min EHR), writes Parquet to `data/lake/bronze/{eeg,ehr}/`
   partitioned by `site_id, ingestion_date`, with checkpointing under
   `data/checkpoints/`.
2. Parallel DLQ stream for rows where `patient_id` or `session_id` is null.
3. `scripts/replay_to_kafka.py` orchestrates Kim-Quan's EEG producer + EHR
   normaliser. Supports `--fallback` (file mode, no Kafka), `--replay-speed`
   (Nx realtime), and a batch-all mode.
4. Review every other member's PR within 24h.

**Acceptance**
- `pytest tests/test_bronze_ingest.py` (Trang writes the test) green.
- End-to-end demo: download → replay → bronze parquet appears on disk.

---

## Work package 2 — Kim-Hung

**Files you own**
- `src/brainwatch/ingestion/kafka_helpers.py`
- `src/brainwatch/ingestion/bronze_writer.py`

**Deliverables**
1. `kafka_helpers.py`:
   - `event_to_bytes(event)` / `bytes_to_dict(raw)` — JSON serde for dataclass events.
   - `create_producer(...)` / `create_consumer(...)` — thin `kafka-python` wrappers
     with `acks=all`, `retries=3`.
   - `FileProducer` — drop-in replacement that appends JSON lines to a local file
     (this is the contract that lets the test suite run **without** Kafka).
   - `get_producer(...)` — try real Kafka, fall back to `FileProducer` on `ImportError`
     or connection failure.
2. `bronze_writer.py`:
   - `BronzeWriter(bronze_root, dlq=...)` class with `write_eeg` / `write_ehr`
     / `write_raw` methods.
   - Validate against `EEG_REQUIRED` / `EHR_REQUIRED` field sets — route invalid
     to the DLQ Dat builds.
   - Dedup via sha256 of `patient_id|session_id_or_encounter_id|event_time` (first
     16 hex chars).
   - Partition layout: `{stream}/site={site_id}/date=YYYY-MM-DD/*.jsonl` for EEG,
     `{stream}/date=YYYY-MM-DD/*.jsonl` for EHR.
   - Expose `.stats` → `{written, duplicates, errors}`.

**Acceptance**
- Tests Trang writes (`test_kafka_helpers.py`, `test_bronze_writer.py`) green.
- Manual: `BronzeWriter` writes one EEG event, ignores the duplicate on second call.

---

## Work package 3 — Kim-Quan

**Files you own**
- `src/brainwatch/ingestion/eeg_producer.py`
- `src/brainwatch/ingestion/ehr_normalizer.py`

**Deliverables**
1. `eeg_producer.py`:
   - `manifest_to_events(manifest_path)` — read the JSON manifest produced by
     `scripts/download_eeg_ehr.py`, return a list of `EEGChunkEvent`.
   - `publish_events(events, topic, bootstrap_servers, replay_speed=0, fallback_path=None)`
     — uses `get_producer` from Kim-Hung. `replay_speed=0` is batch-all,
     `replay_speed=N` sleeps between events to simulate Nx realtime.
   - Returns `{published, failed, validation_errors}` stats.
2. `ehr_normalizer.py`:
   - `generate_ehr_from_manifest(manifest_path, events_per_subject=5)` — emit
     synthetic EHR events spread across event types: `vital_signs`, `lab_result`,
     `medication`, `critical_lab`, `note`. Fields must match `EHREvent`.
   - `normalize_ehr_payload(raw)` — coerce a free-form dict into a valid
     `EHREvent` (lowercase `event_type`, ISO `event_time`, default `version=1`).
   - `publish_ehr_events(...)` mirrors `publish_events` for the `ehr.updates` topic.

**Acceptance**
- Unit tests green.
- Generated EHR events distribute reasonably across the 5 event types
  (no type holds > 60% of the stream).

---

## Work package 4 — Dat

**Files you own**
- `src/brainwatch/ingestion/dead_letter.py`
- `infra/docker/docker-compose.yml`

**Deliverables**
1. `dead_letter.py`:
   - `DeadLetterQueue(output_dir)` — append-only JSONL, one file per UTC day:
     `dead_letter_YYYY-MM-DD.jsonl`.
   - `route(payload, reason)` — wraps the payload in
     `{"routed_at": iso_now, "reason": ..., "original_payload": ...}` and writes
     a line. Bumps an internal counter.
   - `read_all()` — iterate every `dead_letter_*.jsonl` file in sorted order,
     return list of records (for inspection / replay).
2. `infra/docker/docker-compose.yml`:
   - Apache Kafka 3.9 in **KRaft mode** (no Zookeeper).
   - `provectuslabs/kafka-ui` on port 8080.
   - Apache Spark 3.5 master (UI on 8081) + 1 worker.
   - Auto-create topics, internal listener `kafka:9092`, external `localhost:9094`.

**Acceptance**
- `pytest tests/test_dead_letter.py` green.
- `docker compose up -d` brings the stack up cleanly; `kafka-topics.sh --list`
  works from inside the kafka container.

---

## Work package 5 — Trang

**Files you own**
- `scripts/download_eeg_ehr.py` — main Week-2 CLI, with the credentials loader
  (Quang-Hung will pair on the boto3 part the first time)
- All Week-2 unit tests in `tests/`

**Deliverables**
1. `scripts/download_eeg_ehr.py`:
   - `--csv-dir` points at the BDSP metadata CSVs (sibling repo
     `../STELAR-private/pretrain/reve/metadata`).
   - `--credentials` defaults to
     `/mnt/disk1/aiotlab/pqhung/ipp-proposal/credentials/rootkey.csv` (or
     `$BDSP_CREDENTIALS`).
   - `--target-hours` (default 100), `--min-duration`, `--max-duration` filters.
   - `--dry-run` prints the manifest only.
   - `--download` actually pulls EDF files via boto3 to `--download-root data/raw/eeg`.
   - Also calls Kim-Quan's `generate_ehr_from_manifest` and writes
     `artifacts/week2/synthetic_ehr.jsonl`.
2. Tests:
   - `tests/test_kafka_helpers.py` (covers Kim-Hung's serde + FileProducer)
   - `tests/test_bronze_writer.py` (covers Kim-Hung's writer + dedup)
   - `tests/test_eeg_producer.py` (Kim-Quan)
   - `tests/test_ehr_normalizer.py` (Kim-Quan)
   - `tests/test_dead_letter.py` (Dat)
   - `tests/test_download_eeg_ehr.py` (your own script — manifest builder logic,
     no live S3)
   - `tests/test_bronze_ingest.py` (Quang-Hung's Spark code; can use a stub
     SparkSession or skip if `pyspark` is not importable).

**Acceptance**
- `pytest -v` reports ≥ 24 tests, all green.
- Running the CLI in `--dry-run` mode prints a manifest with ~1,400 subjects
  across 5 sites.

---

## Definition of Done (whole team)

- [ ] `pytest -v` green on `main` after every merged PR.
- [ ] `python scripts/download_eeg_ehr.py --dry-run` prints a sane manifest.
- [ ] `python scripts/replay_to_kafka.py --manifest <m> --fallback` produces
      `artifacts/week2/kafka_fallback.jsonl` with both `eeg.raw` and `ehr.updates`
      records.
- [ ] `docker compose up` brings up Kafka + Spark with no errors.
- [ ] Each member opens at least one PR into `main`.
- [ ] Quang-Hung writes the Week-2 demo slides (`docs/week2-slides.md`) once
      everything integrates.

## Cadence

- **Mon/Wed/Fri 19:00** — 15-min standup on Discord. Each member reports
  yesterday/today/blockers in two sentences.
- **Sun 21:00** — code freeze for the week. Quang-Hung merges + tags the demo.

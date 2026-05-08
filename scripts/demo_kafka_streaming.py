#!/usr/bin/env python3
"""End-to-end Kafka streaming demo for BrainWatch.

Demonstrates the full pipeline:
  1. Generate EEG + EHR events from the download manifest
  2. Publish them to Kafka topics (eeg.raw, ehr.updates)
  3. Consume and display events in real-time
  4. Write validated events to the bronze zone via BronzeWriter
  5. Show dead-letter queue for invalid events

Prerequisites:
  docker compose -f infra/docker/docker-compose.yml up -d
  pip install -e ".[dev]"

Usage:
  python scripts/demo_kafka_streaming.py
  python scripts/demo_kafka_streaming.py --no-kafka   # file-fallback mode (no Docker needed)
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

# ── BrainWatch imports ──────────────────────────────────────────────
from brainwatch.contracts.events import (
    EEGChunkEvent,
    EHREvent,
    AlertEvent,
    to_payload,
    validate_required_fields,
)
from brainwatch.ingestion.kafka_helpers import (
    create_consumer,
    event_to_bytes,
    get_producer,
)
from brainwatch.ingestion.eeg_producer import manifest_to_events
from brainwatch.ingestion.ehr_normalizer import generate_ehr_from_manifest
from brainwatch.ingestion.bronze_writer import BronzeWriter
from brainwatch.ingestion.dead_letter import DeadLetterQueue

# ── Constants ───────────────────────────────────────────────────────
MANIFEST = Path("artifacts/week2/download_manifest.json")
BRONZE_ROOT = Path("data/lake/bronze")
EEG_TOPIC = "eeg.raw"
EHR_TOPIC = "ehr.updates"
ALERT_TOPIC = "alerts.anomaly"
DLQ_TOPIC = "dead.letter"
BOOTSTRAP = "localhost:9094"          # external listener from docker-compose
MAX_DEMO_EVENTS = 10                  # limit for a quick demo
CONSUME_TIMEOUT_SEC = 8


# ── Helpers ─────────────────────────────────────────────────────────
def banner(title: str) -> None:
    width = 60
    print(f"\n{'=' * width}")
    print(f"  {title}")
    print(f"{'=' * width}")


def pretty(obj: dict, indent: int = 2) -> str:
    return json.dumps(obj, indent=indent, default=str)


# ── Step 1: Generate events ────────────────────────────────────────
def generate_events(manifest: Path, limit: int) -> tuple[list[EEGChunkEvent], list[EHREvent]]:
    banner("STEP 1 — Generate events from manifest")
    print(f"  Manifest : {manifest}")

    eeg_events = manifest_to_events(manifest)[:limit]
    ehr_events = generate_ehr_from_manifest(manifest, events_per_subject=2)[:limit]

    print(f"  EEG events: {len(eeg_events)}")
    print(f"  EHR events: {len(ehr_events)}")
    print(f"\n  Sample EEG event:")
    print(f"  {pretty(to_payload(eeg_events[0]))}")
    print(f"\n  Sample EHR event:")
    print(f"  {pretty(to_payload(ehr_events[0]))}")
    return eeg_events, ehr_events


# ── Step 2: Publish to Kafka ───────────────────────────────────────
def publish_to_kafka(
    eeg_events: list[EEGChunkEvent],
    ehr_events: list[EHREvent],
    bootstrap: str,
    fallback: str | None,
) -> None:
    banner("STEP 2 — Publish events to Kafka")
    producer = get_producer(bootstrap, fallback_path=fallback)

    eeg_sent = 0
    for ev in eeg_events:
        producer.send(EEG_TOPIC, value=ev)
        eeg_sent += 1
    print(f"  -> Sent {eeg_sent} EEG events to topic '{EEG_TOPIC}'")

    ehr_sent = 0
    for ev in ehr_events:
        producer.send(EHR_TOPIC, value=ev)
        ehr_sent += 1
    print(f"  -> Sent {ehr_sent} EHR events to topic '{EHR_TOPIC}'")

    producer.flush()
    producer.close()
    print("  Producer flushed and closed.")


# ── Step 3: Consume from Kafka ─────────────────────────────────────
def consume_and_display(bootstrap: str, topic: str, label: str, results: list) -> None:
    """Consume messages from a topic (runs in a thread)."""
    try:
        consumer = create_consumer(
            topic,
            bootstrap_servers=bootstrap,
            group_id=f"demo-{topic}-{int(time.time())}",
            consumer_timeout_ms=CONSUME_TIMEOUT_SEC * 1000,
        )
        for msg in consumer:
            results.append(msg.value)
        consumer.close()
    except Exception as exc:
        print(f"  [{label}] Consumer error: {exc}")


def consume_demo(bootstrap: str) -> tuple[list[dict], list[dict]]:
    banner("STEP 3 — Consume events from Kafka")
    print(f"  Listening for {CONSUME_TIMEOUT_SEC}s on topics: {EEG_TOPIC}, {EHR_TOPIC} ...")

    eeg_results: list[dict] = []
    ehr_results: list[dict] = []

    t1 = threading.Thread(target=consume_and_display, args=(bootstrap, EEG_TOPIC, "EEG", eeg_results))
    t2 = threading.Thread(target=consume_and_display, args=(bootstrap, EHR_TOPIC, "EHR", ehr_results))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    print(f"  Consumed {len(eeg_results)} EEG + {len(ehr_results)} EHR messages")
    if eeg_results:
        print(f"\n  First consumed EEG message:")
        print(f"  {pretty(eeg_results[0])}")
    if ehr_results:
        print(f"\n  First consumed EHR message:")
        print(f"  {pretty(ehr_results[0])}")
    return eeg_results, ehr_results


# ── Step 4: Bronze zone write ──────────────────────────────────────
def write_bronze(eeg_events: list[EEGChunkEvent], ehr_events: list[EHREvent]) -> None:
    banner("STEP 4 — Write to Bronze zone (validated + deduplicated)")
    dlq = DeadLetterQueue(BRONZE_ROOT / "_dead_letter")
    writer = BronzeWriter(BRONZE_ROOT, dlq=dlq)

    for ev in eeg_events:
        writer.write_eeg(ev)
    for ev in ehr_events:
        writer.write_ehr(ev)

    print(f"  Bronze stats: {writer.stats}")
    print(f"  Bronze root : {BRONZE_ROOT.resolve()}")


# ── Step 5: Dead-letter demo ───────────────────────────────────────
def dead_letter_demo() -> None:
    banner("STEP 5 — Dead-letter queue demo (invalid events)")
    dlq = DeadLetterQueue(BRONZE_ROOT / "_dead_letter")
    writer = BronzeWriter(BRONZE_ROOT, dlq=dlq)

    # Intentionally bad events
    bad_eeg = EEGChunkEvent(
        patient_id="",          # missing!
        session_id="",          # missing!
        event_time="",          # missing!
        site_id="",
        channel_count=19,
        sampling_rate_hz=200.0,
        window_seconds=30.0,
        source_uri="s3://fake/path.edf",
    )
    result = writer.write_eeg(bad_eeg)
    print(f"  Wrote bad EEG event -> accepted: {result}")
    print(f"  Writer stats: {writer.stats}")

    records = dlq.read_all()
    if records:
        print(f"  DLQ has {len(records)} record(s). Latest:")
        print(f"  {pretty(records[-1])}")


# ── Step 6: Alert event demo ───────────────────────────────────────
def alert_demo(bootstrap: str, fallback: str | None) -> None:
    banner("STEP 6 — Anomaly alert event")
    alert = AlertEvent(
        patient_id="sub-I0002150008034",
        session_id="17",
        alert_time=datetime.now(tz=timezone.utc).isoformat(),
        severity="HIGH",
        anomaly_score=0.92,
        explanation="Sustained high-frequency burst detected in channels Fp1, Fp2",
    )
    print(f"  Alert payload:")
    print(f"  {pretty(to_payload(alert))}")

    producer = get_producer(bootstrap, fallback_path=fallback)
    producer.send(ALERT_TOPIC, value=alert)
    producer.flush()
    producer.close()
    print(f"  -> Published to '{ALERT_TOPIC}'")


# ── File-fallback mode ─────────────────────────────────────────────
def run_fallback_mode(eeg_events, ehr_events, fallback_path):
    banner("STEP 2+3 — File-fallback mode (no Kafka)")
    publish_to_kafka(eeg_events, ehr_events, BOOTSTRAP, fallback_path)
    print(f"\n  Fallback file written to: {fallback_path}")
    path = Path(fallback_path)
    if path.exists():
        lines = path.read_text().strip().split("\n")
        print(f"  Total records in file: {len(lines)}")
        print(f"\n  First record:")
        print(f"  {pretty(json.loads(lines[0]))}")


# ── Main ────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="BrainWatch Kafka streaming demo")
    parser.add_argument("--no-kafka", action="store_true",
                        help="Use file fallback instead of real Kafka")
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--limit", type=int, default=MAX_DEMO_EVENTS,
                        help="Max events to generate per stream")
    parser.add_argument("--bootstrap", default=BOOTSTRAP)
    args = parser.parse_args()

    fallback_path = "artifacts/week2/demo_fallback.jsonl" if args.no_kafka else None

    print("=" * 60)
    print("  BrainWatch — Kafka Streaming Demo")
    print(f"  Mode: {'FILE FALLBACK' if args.no_kafka else 'KAFKA (Docker)'}")
    print(f"  Time: {datetime.now(tz=timezone.utc).isoformat()}")
    print("=" * 60)

    # Step 1: Generate
    eeg_events, ehr_events = generate_events(args.manifest, args.limit)

    if args.no_kafka:
        # File-fallback path (no Docker needed)
        run_fallback_mode(eeg_events, ehr_events, fallback_path)
    else:
        # Real Kafka path
        publish_to_kafka(eeg_events, ehr_events, args.bootstrap, fallback=None)
        consume_demo(args.bootstrap)

    # Step 4+5: Bronze + DLQ (always runs)
    write_bronze(eeg_events, ehr_events)
    dead_letter_demo()

    # Step 6: Alert
    alert_demo(args.bootstrap, fallback_path)

    banner("DEMO COMPLETE")
    print("  Kafka UI:      http://localhost:8890")
    print("  Spark Master:  http://localhost:8891")
    print(f"  Bronze zone:   {BRONZE_ROOT.resolve()}")
    print()


if __name__ == "__main__":
    main()

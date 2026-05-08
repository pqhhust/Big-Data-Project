"""EEG metadata-to-event publisher.

Reads a download manifest (or metadata CSVs) and publishes EEGChunkEvent
messages to the ``eeg.raw`` Kafka topic.  Supports:
  - **batch** mode: publish all events as fast as possible
  - **replay** mode: sleep proportionally to simulate real-time arrival
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from brainwatch.contracts.events import EEGChunkEvent, to_payload, validate_required_fields
from brainwatch.ingestion.kafka_helpers import get_producer

EEG_REQUIRED = {"patient_id", "session_id", "event_time", "site_id"}


def manifest_to_events(manifest_path: Path) -> list[EEGChunkEvent]:
    """Convert a download manifest into a list of EEGChunkEvents."""
    with manifest_path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)

    events: list[EEGChunkEvent] = []
    for rec in data.get("records", []):
        ev = EEGChunkEvent(
            patient_id=rec.get("subject_id", ""),
            session_id=rec.get("session_id", ""),
            event_time=datetime.now(tz=timezone.utc).isoformat(),
            site_id=rec.get("site_id", ""),
            channel_count=19,
            sampling_rate_hz=200.0,
            window_seconds=rec.get("duration_seconds", 0.0),
            source_uri=rec.get("s3_keys", [""])[0],
        )
        events.append(ev)
    return events


def publish_events(
    events: list[EEGChunkEvent],
    topic: str = "eeg.raw",
    bootstrap_servers: str = "localhost:9092",
    replay_speed: float = 0.0,
    fallback_path: str | None = None,
) -> dict[str, Any]:
    """Publish events to Kafka (or file fallback). Returns summary stats."""
    producer = get_producer(bootstrap_servers, fallback_path=fallback_path)
    sent, errors = 0, 0

    for ev in events:
        payload = to_payload(ev)
        missing = validate_required_fields(payload, EEG_REQUIRED)
        if missing:
            errors += 1
            continue
        producer.send(topic, value=ev)
        sent += 1
        if replay_speed > 0:
            time.sleep(ev.window_seconds / replay_speed)

    producer.flush()
    producer.close()
    return {"sent": sent, "errors": errors, "total": len(events)}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Publish EEG events to Kafka.")
    p.add_argument("--manifest", required=True, type=Path, help="Download manifest JSON")
    p.add_argument("--topic", default="eeg.raw")
    p.add_argument("--bootstrap-servers", default="localhost:9092")
    p.add_argument("--replay-speed", type=float, default=0.0,
                    help="Replay speedup factor (0 = batch, 10 = 10x realtime)")
    p.add_argument("--fallback-path", default=None,
                    help="File path for Kafka-unavailable fallback")
    return p


def main() -> None:
    args = build_parser().parse_args()
    events = manifest_to_events(args.manifest)
    print(f"Loaded {len(events)} EEG events from {args.manifest}")
    stats = publish_events(
        events,
        topic=args.topic,
        bootstrap_servers=args.bootstrap_servers,
        replay_speed=args.replay_speed,
        fallback_path=args.fallback_path,
    )
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()

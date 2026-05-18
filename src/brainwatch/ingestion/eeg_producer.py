"""EEG metadata-to-event publisher.

Owner: **Kim-Quan**.
Depends on: Kim-Hung's ``kafka_helpers.get_producer``, and the
canonical ``EEGChunkEvent`` schema in ``brainwatch.contracts.events``.

Reads a download manifest produced by ``scripts/download_eeg_ehr.py`` (Trang)
and publishes ``EEGChunkEvent`` messages to the ``eeg.raw`` Kafka topic.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from brainwatch.contracts.events import EEGChunkEvent
from brainwatch.ingestion.kafka_helpers import get_producer

EEG_REQUIRED = {"patient_id", "session_id", "event_time", "site_id"}


def manifest_to_events(manifest_path: Path) -> list[EEGChunkEvent]:
    """Read the JSON manifest and turn each record into an ``EEGChunkEvent``."""
    with manifest_path.open() as f:
        manifest = json.load(f)

    events = []
    for record in manifest.get("records", []):
        event = EEGChunkEvent(
            patient_id=record["subject_id"],
            session_id=record["session_id"],
            event_time=datetime.now(timezone.utc).isoformat(),
            site_id=record["site_id"],
            channel_count=19,
            sampling_rate_hz=200.0,
            window_seconds=record["duration_seconds"],
            source_uri=record["s3_keys"][0] if record["s3_keys"] else ""
        )
        events.append(event)
    return events


def publish_events(
    events: list[EEGChunkEvent],
    topic: str = "eeg.raw",
    bootstrap_servers: str = "localhost:9092",
    replay_speed: float = 0.0,
    fallback_path: str | None = None,
) -> dict[str, Any]:
    """Publish events to Kafka (or the file fallback). Returns stats dict."""
    from dataclasses import asdict
    producer = get_producer(bootstrap_servers, fallback_path)

    stats = {"published": 0, "failed": 0, "validation_errors": 0}

    for event in events:
        payload = asdict(event)
        missing = [f for f in EEG_REQUIRED if payload.get(f) in (None, "")]
        if missing:
            stats["validation_errors"] += 1
            continue

        try:
            producer.send(topic, value=payload)
            stats["published"] += 1

            if replay_speed > 0:
                sleep_time = event.window_seconds / replay_speed
                time.sleep(sleep_time)
        except Exception:
            stats["failed"] += 1

    producer.flush()
    producer.close()
    return stats
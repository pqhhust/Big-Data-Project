"""Tests for ``brainwatch.ingestion.eeg_producer``.

Owner: **Trang**.  Covers Kim-Quan's manifest -> events -> publish path.
"""
from __future__ import annotations

import json
from pathlib import Path

from brainwatch.ingestion.eeg_producer import manifest_to_events, publish_events
from brainwatch.ingestion.kafka_helpers import FileProducer


def _write_manifest(tmp_path: Path, n_records: int = 3) -> Path:
    """Write a tiny manifest JSON with n_records records."""
    records = []
    for i in range(n_records):
        records.append({
            "subject_id": f"P{i:03d}",
            "session_id": f"S{i:03d}",
            "site_id": f"SITE{i:02d}",
            "duration_seconds": 30.0 + i * 10,
            "s3_keys": [f"s3://bucket/file_{i}.edf"]
        })
    manifest = {
        "target_hours": 1,
        "actual_hours": 0.5,
        "site_count": 3,
        "subject_count": n_records,
        "records": records
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    return manifest_path


def test_manifest_to_events_maps_fields(tmp_path: Path):
    """Write a 1-record manifest, verify EEGChunkEvent field mapping."""
    manifest_path = _write_manifest(tmp_path, n_records=1)
    events = manifest_to_events(manifest_path)

    assert len(events) == 1
    event = events[0]

    assert event.patient_id == "P000"
    assert event.session_id == "S000"
    assert event.site_id == "SITE00"
    assert event.channel_count == 19
    assert event.sampling_rate_hz == 200.0
    assert event.window_seconds == 30.0
    assert event.source_uri == "s3://bucket/file_0.edf"


def test_publish_events_uses_file_fallback(tmp_path: Path):
    """Publish events to file fallback, verify output."""
    manifest_path = _write_manifest(tmp_path, n_records=3)
    events = manifest_to_events(manifest_path)

    # Use invalid bootstrap servers to force fallback
    fallback_path = tmp_path / "fallback.jsonl"
    stats = publish_events(
        events,
        bootstrap_servers="invalid:9999",
        fallback_path=str(fallback_path)
    )

    assert stats["published"] == 3
    assert stats["failed"] == 0
    assert stats["validation_errors"] == 0

    content = fallback_path.read_text().strip().split("\n")
    assert len(content) == 3


def test_publish_events_counts_validation_errors(tmp_path: Path):
    """Events with missing fields should count as validation errors."""
    from brainwatch.contracts.events import EEGChunkEvent

    # Event with empty session_id
    events = [
        EEGChunkEvent(
            patient_id="P001",
            session_id="",  # Empty - invalid
            event_time="2026-05-19T10:00:00Z",
            site_id="SITE01",
            channel_count=19,
            sampling_rate_hz=200.0,
            window_seconds=30.0,
            source_uri="s3://bucket/file.edf"
        )
    ]

    fallback_path = tmp_path / "fallback.jsonl"
    stats = publish_events(events, fallback_path=str(fallback_path))

    assert stats["validation_errors"] == 1
    assert stats["published"] == 0
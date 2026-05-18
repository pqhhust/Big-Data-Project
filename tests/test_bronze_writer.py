"""Tests for ``brainwatch.ingestion.bronze_writer``.

Owner: **Trang**.  Covers Kim-Hung's ``BronzeWriter``.
"""
from __future__ import annotations

from pathlib import Path

from brainwatch.ingestion.bronze_writer import BronzeWriter
from brainwatch.contracts.events import EEGChunkEvent, EHREvent


def _make_eeg_event(**overrides):
    """Helper returning a valid EEGChunkEvent with sane defaults."""
    defaults = {
        "patient_id": "P001",
        "session_id": "S001",
        "event_time": "2026-05-19T10:00:00Z",
        "site_id": "SITE01",
        "channel_count": 19,
        "sampling_rate_hz": 200.0,
        "window_seconds": 30.0,
        "source_uri": "s3://bucket/file.edf"
    }
    defaults.update(overrides)
    return EEGChunkEvent(**defaults)


def test_writes_eeg_event_to_partitioned_path(tmp_path: Path):
    """Write one valid EEG event, verify partitioned path exists."""
    writer = BronzeWriter(tmp_path)
    event = _make_eeg_event()

    result = writer.write_eeg(event)
    assert result is True

    stats = writer.stats
    assert stats["written"] == 1
    assert stats["duplicates"] == 0
    assert stats["errors"] == 0

    # Check partition path exists
    import json
    bronze_dir = tmp_path / "eeg"
    assert bronze_dir.exists()

    # Find and read the jsonl file
    date_dirs = list(bronze_dir.glob("site=SITE01/date=*"))
    assert len(date_dirs) == 1

    jsonl_file = date_dirs[0].glob("*.jsonl").__iter__().__next__()
    content = jsonl_file.read_text().strip()
    record = json.loads(content)
    assert record["patient_id"] == "P001"
    assert record["site_id"] == "SITE01"


def test_dedup_ignores_duplicate_events(tmp_path: Path):
    """Write the same event twice, second should be deduped."""
    writer = BronzeWriter(tmp_path)
    event = _make_eeg_event()

    # First write succeeds
    assert writer.write_eeg(event) is True
    # Second write is duplicate
    assert writer.write_eeg(event) is False

    stats = writer.stats
    assert stats["written"] == 1
    assert stats["duplicates"] == 1
    assert stats["errors"] == 0


def test_invalid_event_routed_to_dlq(tmp_path: Path):
    """Write invalid event missing site_id, verify DLQ routing."""
    writer = BronzeWriter(tmp_path)

    # Missing site_id
    invalid_event = {
        "patient_id": "P001",
        "session_id": "S001",
        "event_time": "2026-05-19T10:00:00Z"
        # site_id is missing
    }

    result = writer.write_raw("eeg", invalid_event)
    assert result is False

    stats = writer.stats
    assert stats["errors"] == 1
    assert stats["written"] == 0

    # Check DLQ file exists
    dlq_dir = tmp_path / "_dead_letter"
    assert dlq_dir.exists()

    dlq_files = list(dlq_dir.glob("dead_letter_*.jsonl"))
    assert len(dlq_files) >= 1


def test_ehr_event_partitions_by_date_only(tmp_path: Path):
    """EHR has no site_id partition, only date."""
    writer = BronzeWriter(tmp_path)
    event = EHREvent(
        patient_id="P001",
        encounter_id="ENC001",
        event_time="2026-05-19T10:00:00Z",
        event_type="vital_signs",
        source_system="epic",
        version=1,
        payload={"hr": 72}
    )

    result = writer.write_ehr(event)
    assert result is True

    stats = writer.stats
    assert stats["written"] == 1

    # EHR should not have site= partition
    bronze_dir = tmp_path / "ehr"
    assert bronze_dir.exists()

    # Should have date= dirs directly, not site= prefix
    date_dirs = [d for d in bronze_dir.iterdir() if d.name.startswith("date=")]
    assert len(date_dirs) >= 1
from pathlib import Path

from brainwatch.contracts.events import EEGChunkEvent, EHREvent
from brainwatch.ingestion.bronze_writer import BronzeWriter


def _make_eeg(pid: str = "P1", sid: str = "S1") -> EEGChunkEvent:
    return EEGChunkEvent(
        patient_id=pid, session_id=sid, event_time="2026-01-01T00:00:00",
        site_id="S0001", channel_count=19, sampling_rate_hz=200.0,
        window_seconds=300.0, source_uri="s3://bucket/key.edf",
    )


def _make_ehr(pid: str = "P1") -> EHREvent:
    return EHREvent(
        patient_id=pid, encounter_id="E1", event_time="2026-01-01T00:00:00",
        event_type="admission", source_system="test", version=1, payload={},
    )


def test_write_eeg_creates_partitioned_file(tmp_path: Path) -> None:
    writer = BronzeWriter(tmp_path / "bronze")
    assert writer.write_eeg(_make_eeg())
    assert writer.stats["written"] == 1
    # Check partitioned path exists
    eeg_dir = tmp_path / "bronze" / "eeg"
    assert eeg_dir.exists()


def test_dedup_prevents_second_write(tmp_path: Path) -> None:
    writer = BronzeWriter(tmp_path / "bronze")
    assert writer.write_eeg(_make_eeg())
    assert not writer.write_eeg(_make_eeg())  # same event = duplicate
    assert writer.stats["written"] == 1
    assert writer.stats["duplicates"] == 1


def test_invalid_eeg_routed_to_dlq(tmp_path: Path) -> None:
    writer = BronzeWriter(tmp_path / "bronze")
    bad = EEGChunkEvent(
        patient_id="", session_id="S1", event_time="2026-01-01",
        site_id="S0001", channel_count=19, sampling_rate_hz=200.0,
        window_seconds=300.0, source_uri="",
    )
    assert not writer.write_eeg(bad)
    assert writer.stats["errors"] == 1


def test_write_ehr(tmp_path: Path) -> None:
    writer = BronzeWriter(tmp_path / "bronze")
    assert writer.write_ehr(_make_ehr())
    assert writer.stats["written"] == 1

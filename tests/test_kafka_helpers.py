import json
from pathlib import Path

from brainwatch.contracts.events import EEGChunkEvent
from brainwatch.ingestion.kafka_helpers import FileProducer, bytes_to_dict, event_to_bytes


def test_event_to_bytes_roundtrip() -> None:
    ev = EEGChunkEvent(
        patient_id="P001", session_id="S1", event_time="2026-01-01T00:00:00",
        site_id="S0001", channel_count=19, sampling_rate_hz=200.0,
        window_seconds=300.0, source_uri="s3://bucket/key.edf",
    )
    raw = event_to_bytes(ev)
    restored = bytes_to_dict(raw)
    assert restored["patient_id"] == "P001"
    assert restored["channel_count"] == 19


def test_file_producer_writes_jsonl(tmp_path: Path) -> None:
    out = tmp_path / "test.jsonl"
    producer = FileProducer(str(out))
    producer.send("eeg.raw", value={"patient_id": "P1", "session_id": "S1"})
    producer.send("eeg.raw", value={"patient_id": "P2", "session_id": "S2"})
    producer.flush()
    producer.close()

    lines = out.read_text().strip().split("\n")
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["topic"] == "eeg.raw"
    assert first["value"]["patient_id"] == "P1"

import json
from pathlib import Path

from brainwatch.ingestion.eeg_producer import manifest_to_events, publish_events


def _write_manifest(tmp_path: Path) -> Path:
    manifest = {
        "manifest_version": "2.0",
        "summary": {},
        "records": [
            {
                "site_id": "S0001",
                "subject_id": "sub-S0001AAA",
                "session_id": "1",
                "duration_seconds": 600.0,
                "service_name": "LTM",
                "s3_keys": ["EEG/bids/S0001/sub-S0001AAA/ses-1/eeg/file.edf"],
            },
            {
                "site_id": "S0002",
                "subject_id": "sub-S0002BBB",
                "session_id": "2",
                "duration_seconds": 300.0,
                "service_name": "Routine",
                "s3_keys": ["EEG/bids/S0002/sub-S0002BBB/ses-2/eeg/file.edf"],
            },
        ],
    }
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(manifest))
    return p


def test_manifest_to_events(tmp_path: Path) -> None:
    mf = _write_manifest(tmp_path)
    events = manifest_to_events(mf)
    assert len(events) == 2
    assert events[0].patient_id == "sub-S0001AAA"
    assert events[0].site_id == "S0001"
    assert events[0].channel_count == 19


def test_publish_events_file_fallback(tmp_path: Path) -> None:
    mf = _write_manifest(tmp_path)
    events = manifest_to_events(mf)
    fallback = str(tmp_path / "fallback.jsonl")
    stats = publish_events(events, fallback_path=fallback)
    assert stats["sent"] == 2
    assert stats["errors"] == 0
    lines = Path(fallback).read_text().strip().split("\n")
    assert len(lines) == 2

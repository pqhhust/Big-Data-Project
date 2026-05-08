import json
from pathlib import Path

from brainwatch.ingestion.ehr_normalizer import (
    generate_ehr_for_subject,
    generate_ehr_from_manifest,
    normalise_raw_ehr,
    write_ehr_bronze,
)


def test_generate_ehr_for_subject_deterministic() -> None:
    events_a = generate_ehr_for_subject("sub-S0001AAA", "S0001", n_events=3)
    events_b = generate_ehr_for_subject("sub-S0001AAA", "S0001", n_events=3)
    assert len(events_a) == 3
    # Deterministic seed → same events
    assert events_a[0].event_type == events_b[0].event_type
    assert events_a[0].patient_id == "sub-S0001AAA"


def test_generate_ehr_from_manifest(tmp_path: Path) -> None:
    manifest = {
        "records": [
            {"subject_id": "sub-A", "site_id": "S0001", "session_id": "1",
             "duration_seconds": 300, "s3_keys": ["k"]},
            {"subject_id": "sub-B", "site_id": "S0002", "session_id": "2",
             "duration_seconds": 600, "s3_keys": ["k"]},
        ],
    }
    p = tmp_path / "m.json"
    p.write_text(json.dumps(manifest))
    events = generate_ehr_from_manifest(p, events_per_subject=3)
    assert len(events) == 6  # 2 subjects × 3 events


def test_normalise_raw_ehr_valid() -> None:
    raw = {
        "patient_id": "P1", "encounter_id": "E1",
        "event_time": "2026-01-01", "event_type": "admission",
        "extra_field": "kept",
    }
    ev = normalise_raw_ehr(raw)
    assert ev is not None
    assert ev.patient_id == "P1"
    assert ev.event_type == "admission"
    assert "extra_field" in ev.payload


def test_normalise_raw_ehr_missing_fields() -> None:
    raw = {"patient_id": "P1"}  # missing encounter_id, event_time, event_type
    assert normalise_raw_ehr(raw) is None


def test_write_ehr_bronze(tmp_path: Path) -> None:
    events = generate_ehr_for_subject("sub-X", "S0001", n_events=2)
    out_path = write_ehr_bronze(events, tmp_path / "bronze")
    assert out_path.exists()
    lines = out_path.read_text().strip().split("\n")
    assert len(lines) == 2

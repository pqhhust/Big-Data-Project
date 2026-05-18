"""Tests for ``brainwatch.ingestion.ehr_normalizer``.

Owner: **Trang**.  Covers Kim-Quan's synthetic generator + normaliser.
"""
from __future__ import annotations

import json
from pathlib import Path

from brainwatch.ingestion.ehr_normalizer import (
    generate_ehr_from_manifest,
    normalize_ehr_payload,
    publish_ehr_events
)
from brainwatch.contracts.events import EHREvent


def _write_manifest(tmp_path: Path, n_subjects: int = 2) -> Path:
    """Write a small manifest with n_subjects records."""
    records = []
    for i in range(n_subjects):
        records.append({
            "subject_id": f"P{i:03d}",
            "session_id": f"S{i:03d}",
            "site_id": f"SITE{i:02d}",
            "duration_seconds": 30.0,
            "s3_keys": [f"s3://bucket/file_{i}.edf"]
        })
    manifest = {
        "target_hours": 1,
        "actual_hours": 0.5,
        "site_count": n_subjects,
        "subject_count": n_subjects,
        "records": records
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    return manifest_path


def test_generate_ehr_emits_expected_count(tmp_path: Path):
    """Generate EHR events for 2 subjects with 4 events each."""
    manifest_path = _write_manifest(tmp_path, n_subjects=2)
    events = generate_ehr_from_manifest(manifest_path, events_per_subject=4)

    assert len(events) == 8  # 2 subjects * 4 events


def test_generate_ehr_distribution_keeps_critical_lab_rare():
    """critical_lab should be < 15% of total events."""
    import tempfile
    import json

    # Create a manifest with enough subjects for ~500 events
    records = [{"subject_id": f"P{i:03d}", "session_id": f"S{i:03d}",
                "site_id": "SITE00", "duration_seconds": 30.0,
                "s3_keys": ["s3://bucket/file.edf"]}
               for i in range(100)]
    manifest = {
        "target_hours": 10,
        "actual_hours": 5,
        "site_count": 1,
        "subject_count": 100,
        "records": records
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(manifest, f)
        manifest_path = Path(f.name)

    events = generate_ehr_from_manifest(manifest_path, events_per_subject=5)

    critical_count = sum(1 for e in events if e.event_type == "critical_lab")
    critical_pct = critical_count / len(events)

    assert critical_pct < 0.15, f"critical_lab too common: {critical_pct:.1%}"


def test_normalize_lowercases_event_type():
    """event_type should be lowercased."""
    raw = {
        "patient_id": "P001",
        "encounter_id": "ENC001",
        "event_time": "2026-05-19T10:00:00Z",
        "event_type": "VITAL_SIGNS"
    }

    event = normalize_ehr_payload(raw)
    assert event.event_type == "vital_signs"


def test_normalize_raises_on_missing_patient_id():
    """Missing patient_id should raise ValueError."""
    raw = {
        "encounter_id": "ENC001",
        "event_time": "2026-05-19T10:00:00Z",
        "event_type": "note"
    }

    try:
        normalize_ehr_payload(raw)
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "patient_id" in str(e)


def test_normalize_defaults_version_and_source():
    """Missing version and source_system should get defaults."""
    raw = {
        "patient_id": "P001",
        "encounter_id": "ENC001",
        "event_time": "2026-05-19T10:00:00Z"
    }

    event = normalize_ehr_payload(raw)
    assert event.version == 1
    assert event.source_system == "unknown"
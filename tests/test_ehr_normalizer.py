"""Tests for ``brainwatch.ingestion.ehr_normalizer``.

Owner: **Trang**. Covers Kim-Quan's synthetic generator + normaliser.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

from brainwatch.ingestion.ehr_normalizer import (
    generate_ehr_from_manifest,
    normalize_ehr_payload,
)


def _write_manifest(path: Path, record_count: int = 2) -> None:
    records = []
    for index in range(record_count):
        records.append(
            {
                "subject_id": f"sub-{index + 1:02d}",
                "session_id": str(index + 1),
                "site_id": "I0002",
                "duration_seconds": 300.0,
                "candidate_source_keys": [f"I0002/sub-{index + 1:02d}/ses-{index + 1}/eeg/file.edf"],
                "local_target_dir": "data/raw/eeg",
            }
        )
    path.write_text(json.dumps({"record_count": len(records), "records": records}, indent=2), encoding="utf-8")


def test_generate_ehr_emits_expected_count(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    _write_manifest(manifest_path, record_count=2)

    events = generate_ehr_from_manifest(manifest_path, events_per_subject=4)

    assert len(events) == 8
    assert all(event.patient_id.startswith("sub-") for event in events)


def test_generate_ehr_distribution_keeps_critical_lab_rare(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    _write_manifest(manifest_path, record_count=10)

    events = generate_ehr_from_manifest(manifest_path, events_per_subject=50)
    counts = Counter(event.event_type for event in events)

    assert counts["critical_lab"] / len(events) < 0.15


def test_normalize_lowercases_event_type() -> None:
    event = normalize_ehr_payload(
        {
            "patient_id": "sub-01",
            "encounter_id": "enc-01",
            "event_time": datetime(2026, 5, 9, 10, 30, tzinfo=timezone.utc),
            "event_type": "VITAL_SIGNS",
            "source_system": "epic",
            "extra": "value",
        }
    )

    assert event.event_type == "vital_signs"
    assert event.event_time.endswith("Z")
    assert event.payload["extra"] == "value"


def test_normalize_raises_on_missing_patient_id() -> None:
    with pytest.raises(ValueError):
        normalize_ehr_payload(
            {
                "encounter_id": "enc-01",
                "event_time": "2026-05-09T10:30:00Z",
            }
        )

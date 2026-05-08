"""Tests for ``brainwatch.ingestion.ehr_normalizer``.

Owner: **Trang**.  Covers Kim-Quan's synthetic generator + normaliser.
"""
from __future__ import annotations

from pathlib import Path


def test_generate_ehr_emits_expected_count(tmp_path: Path):
    """Trang: build a 2-subject manifest, call generate_ehr_from_manifest with
    events_per_subject=4, assert len(events) == 8."""
    pass


def test_generate_ehr_distribution_keeps_critical_lab_rare():
    """Trang: generate ~500 events, count event types, assert
    ``critical_lab`` is < 15% of the total. Protects against accidental
    over-sampling that would skew downstream alerting."""
    pass


def test_normalize_lowercases_event_type():
    """Trang: pass ``{"event_type": "VITAL_SIGNS", ...}`` to normalize_ehr_payload,
    assert resulting EHREvent.event_type == "vital_signs"."""
    pass


def test_normalize_raises_on_missing_patient_id():
    """Trang: pass a payload without patient_id, expect ValueError."""
    pass

"""Tests for the synthetic-at-scale generators in scripts/generate_demo_data_at_scale.py."""
from __future__ import annotations

import random
from datetime import datetime, timezone

from scripts.generate_demo_data_at_scale import (
    _eeg_chunks_for_record, _ehr_events_for_subject, _EHR_TYPES,
)

REC = {"subject_id": "175270243", "session_id": "3", "site_id": "I0003",
       "duration_seconds": 120.0, "s3_keys": ["sub-I0003175270243"]}
START = datetime(2026, 5, 19, 8, 0, 0, tzinfo=timezone.utc)


def test_eeg_chunk_count_matches_duration_over_window():
    chunks = _eeg_chunks_for_record(REC, window_seconds=10.0, clone_idx=0, start=START)
    assert len(chunks) == 12          # 120s / 10s
    assert all(c.window_seconds == 10.0 for c in chunks)
    assert all(c.site_id == "I0003" for c in chunks)


def test_eeg_clone_idx_changes_patient_and_session():
    a = _eeg_chunks_for_record(REC, 10.0, 0, START)[0]
    b = _eeg_chunks_for_record(REC, 10.0, 7, START)[0]
    assert a.patient_id != b.patient_id
    assert a.session_id != b.session_id


def test_eeg_event_times_are_monotonic():
    chunks = _eeg_chunks_for_record(REC, 10.0, 0, START)
    times = [c.event_time for c in chunks]
    assert times == sorted(times)


def test_eeg_minimum_one_chunk_even_for_short_record():
    short = dict(REC, duration_seconds=3.0)
    chunks = _eeg_chunks_for_record(short, window_seconds=10.0, clone_idx=0, start=START)
    assert len(chunks) == 1


def test_ehr_event_count_and_types():
    rng = random.Random(0)
    events = _ehr_events_for_subject(REC, clone_idx=0, count=12, start=START, rng=rng)
    assert len(events) == 12
    assert all(e.event_type in _EHR_TYPES for e in events)
    assert all(e.patient_id.startswith("175270243") for e in events)


def test_ehr_versions_in_expected_range():
    rng = random.Random(3)
    events = _ehr_events_for_subject(REC, 0, 50, START, rng)
    assert all(1 <= e.version <= 3 for e in events)

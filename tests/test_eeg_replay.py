from __future__ import annotations

from datetime import datetime, timezone

from brainwatch.ingestion.eeg_replay import generate_eeg_chunk_events


def test_generate_eeg_chunk_events_uses_window_seconds_and_duration() -> None:
    record = {
        "site_id": "S0001",
        "subject_id": "sub-123",
        "session_id": "1",
        "duration_seconds": 35.0,
        "candidate_source_keys": ["s3://bucket/key.edf"],
    }
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    events = list(
        generate_eeg_chunk_events(
            record,
            channel_count=19,
            sampling_rate_hz=256.0,
            window_seconds=10.0,
            start_time=start,
        )
    )

    # floor(35/10)=3
    assert len(events) == 3
    assert events[0].patient_id == "sub-123"
    assert events[0].session_id == "1"
    assert events[0].site_id == "S0001"
    assert events[0].source_uri == "s3://bucket/key.edf"
    assert events[0].event_time.endswith("Z")


def test_generate_eeg_chunk_events_skips_invalid_record() -> None:
    record = {"site_id": "S0001", "duration_seconds": 100.0}
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    events = list(
        generate_eeg_chunk_events(
            record,
            channel_count=19,
            sampling_rate_hz=256.0,
            window_seconds=10.0,
            start_time=start,
        )
    )
    assert events == []

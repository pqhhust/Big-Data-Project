"""Contract round-trip + validation tests for the event dataclasses."""
from __future__ import annotations

from brainwatch.contracts.events import (
    AlertEvent, EEGChunkEvent, EHREvent, FeatureEvent,
    to_payload, validate_required_fields,
)


def test_eeg_event_to_payload_round_trip():
    e = EEGChunkEvent("p1", "s1", "2026-05-19T00:00:00Z", "S0001", 19, 200.0, 10.0, "uri")
    p = to_payload(e)
    assert p["patient_id"] == "p1"
    assert p["channel_count"] == 19
    assert p["sampling_rate_hz"] == 200.0
    assert set(p) == {"patient_id", "session_id", "event_time", "site_id",
                      "channel_count", "sampling_rate_hz", "window_seconds", "source_uri"}


def test_ehr_event_to_payload_keeps_nested_payload():
    e = EHREvent("p1", "enc1", "2026-05-19T00:00:00Z", "lab_result", "epic", 2, {"code": "NA", "value": 140})
    p = to_payload(e)
    assert p["payload"]["code"] == "NA"
    assert p["version"] == 2


def test_feature_event_round_trip():
    e = FeatureEvent("p1", "s1", "2026-05-19T00:01:00Z", 0.42, 0.88, {"eeg_chunk_count": 5.0})
    p = to_payload(e)
    assert p["patient_id"] == "p1"
    assert p["anomaly_score"] == 0.42
    assert p["signal_quality_score"] == 0.88
    assert p["feature_values"]["eeg_chunk_count"] == 5.0


def test_alert_event_round_trip():
    e = AlertEvent("p1", "s1", "2026-05-19T00:00:00Z", "critical", 0.91, "why")
    p = to_payload(e)
    assert p["severity"] == "critical"
    assert p["anomaly_score"] == 0.91


def test_validate_required_fields_all_present():
    payload = {"patient_id": "p1", "session_id": "s1", "event_time": "t", "site_id": "S0001"}
    missing = validate_required_fields(payload, {"patient_id", "session_id", "event_time", "site_id"})
    assert missing == []


def test_validate_required_fields_reports_missing_sorted():
    payload = {"patient_id": "p1", "session_id": "", "site_id": None}
    missing = validate_required_fields(payload, {"patient_id", "session_id", "event_time", "site_id"})
    assert missing == ["event_time", "session_id", "site_id"]


def test_validate_treats_empty_string_and_none_as_missing():
    assert validate_required_fields({"a": ""}, {"a"}) == ["a"]
    assert validate_required_fields({"a": None}, {"a"}) == ["a"]
    assert validate_required_fields({"a": 0}, {"a"}) == []  # zero is present


def test_validate_required_fields_empty_requirement_set():
    assert validate_required_fields({"anything": 1}, set()) == []

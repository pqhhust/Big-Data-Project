"""Tests for brainwatch.contracts.validators — 10 test cases."""

from __future__ import annotations

import pytest

from brainwatch.contracts.validators import (
    compute_dedup_key,
    is_non_negative_float,
    is_positive_int,
    is_valid_ehr_event_type,
    is_valid_score,
    is_valid_severity,
    is_valid_timestamp,
    validate_alert_event,
    validate_eeg_event,
    validate_ehr_event,
)


class TestTimestampValidation:
    def test_valid_iso_timestamp(self) -> None:
        assert is_valid_timestamp("2024-01-15T10:30:00") is True

    def test_valid_timestamp_with_tz(self) -> None:
        assert is_valid_timestamp("2024-01-15T10:30:00+00:00") is True

    def test_invalid_timestamp(self) -> None:
        assert is_valid_timestamp("not-a-date") is False

    def test_empty_timestamp(self) -> None:
        assert is_valid_timestamp("") is False


class TestNumericValidation:
    def test_positive_int(self) -> None:
        assert is_positive_int(5) is True
        assert is_positive_int(0) is False
        assert is_positive_int(-1) is False

    def test_non_negative_float(self) -> None:
        assert is_non_negative_float(0.0) is True
        assert is_non_negative_float(3.14) is True
        assert is_non_negative_float(-1.0) is False

    def test_valid_score(self) -> None:
        assert is_valid_score(0.5) is True
        assert is_valid_score(1.5) is False
        assert is_valid_score(-0.1) is False


class TestDomainValidation:
    def test_valid_severity(self) -> None:
        assert is_valid_severity("critical") is True
        assert is_valid_severity("invalid") is False

    def test_valid_ehr_event_type(self) -> None:
        assert is_valid_ehr_event_type("admission") is True
        assert is_valid_ehr_event_type("unknown_type") is False


class TestCompositeValidators:
    def test_validate_eeg_event_valid(self) -> None:
        payload = {
            "patient_id": "P001", "session_id": "S001",
            "event_time": "2024-01-01T00:00:00", "site_id": "S0001",
            "channel_count": 21, "sampling_rate_hz": 256.0,
        }
        errors = validate_eeg_event(payload)
        assert errors == []

    def test_validate_eeg_event_missing_fields(self) -> None:
        payload = {"patient_id": "P001"}
        errors = validate_eeg_event(payload)
        assert len(errors) > 0
        assert "Missing required" in errors[0]

    def test_validate_ehr_event_invalid_type(self) -> None:
        payload = {
            "patient_id": "P001", "encounter_id": "E001",
            "event_time": "2024-01-01T00:00:00", "event_type": "invalid_type",
        }
        errors = validate_ehr_event(payload)
        assert any("Unknown event_type" in e for e in errors)

    def test_validate_alert_event_invalid_score(self) -> None:
        payload = {
            "patient_id": "P001", "session_id": "S001",
            "alert_time": "2024-01-01T00:00:00", "severity": "critical",
            "anomaly_score": 1.5,
        }
        errors = validate_alert_event(payload)
        assert any("anomaly_score" in e for e in errors)

    def test_compute_dedup_key_deterministic(self) -> None:
        data = {"patient_id": "P001", "event_time": "2024-01-01T00:00:00"}
        key1 = compute_dedup_key(data)
        key2 = compute_dedup_key(data)
        assert key1 == key2

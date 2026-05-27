"""Tests for brainwatch.contracts.events — 15 test cases."""

from __future__ import annotations

import json

import pytest

from brainwatch.contracts.events import (
    AlertEvent,
    BronzeEEGRecord,
    BronzeEHRRecord,
    DLQRecord,
    EEGChunkEvent,
    EHREvent,
    FeatureEvent,
    compute_fingerprint,
    to_json,
    to_payload,
    validate_required_fields,
    EEG_REQUIRED_FIELDS,
    EHR_REQUIRED_FIELDS,
    ALERT_REQUIRED_FIELDS,
)


class TestEEGChunkEvent:
    def test_construction(self) -> None:
        event = EEGChunkEvent(
            patient_id="P001", session_id="S001", event_time="2024-01-01T00:00:00",
            site_id="S0001", channel_count=21, sampling_rate_hz=256.0,
            window_seconds=1.0, source_uri="s3://bucket/file.edf",
        )
        assert event.patient_id == "P001"
        assert event.fingerprint != ""

    def test_from_dict(self) -> None:
        data = {
            "patient_id": "P002", "session_id": "S002", "event_time": "2024-01-01T00:00:00",
            "site_id": "S0001", "channel_count": 19, "sampling_rate_hz": 512.0,
            "window_seconds": 2.0, "source_uri": "",
        }
        event = EEGChunkEvent.from_dict(data)
        assert event.channel_count == 19
        assert event.sampling_rate_hz == 512.0

    def test_to_dict_roundtrip(self) -> None:
        event = EEGChunkEvent(
            patient_id="P003", session_id="S003", event_time="2024-06-15T12:30:00",
            site_id="I0002", channel_count=21, sampling_rate_hz=256.0,
            window_seconds=1.0, source_uri="",
        )
        d = event.to_dict()
        restored = EEGChunkEvent.from_dict(d)
        assert restored.patient_id == event.patient_id
        assert restored.session_id == event.session_id


class TestEHREvent:
    def test_construction(self) -> None:
        event = EHREvent(
            patient_id="P001", encounter_id="E001", event_time="2024-01-01T00:00:00",
            event_type="admission", source_system="EMR-A", version=1, payload={"icd10": "G40.0"},
        )
        assert event.fingerprint != ""

    def test_from_dict(self) -> None:
        data = {
            "patient_id": "P001", "encounter_id": "E001", "event_time": "2024-01-01T00:00:00",
            "event_type": "lab_result", "source_system": "LAB", "version": 2,
            "payload": {"test": "CBC"},
        }
        event = EHREvent.from_dict(data)
        assert event.version == 2
        assert event.payload["test"] == "CBC"


class TestFeatureEvent:
    def test_construction_and_from_dict(self) -> None:
        event = FeatureEvent(
            patient_id="P001", session_id="S001", window_end="2024-01-01T00:01:00",
            anomaly_score=0.75, signal_quality_score=0.9, feature_values={"alpha": 0.5},
        )
        restored = FeatureEvent.from_dict(event.to_dict())
        assert restored.anomaly_score == 0.75


class TestAlertEvent:
    def test_auto_alert_id(self) -> None:
        alert = AlertEvent(
            patient_id="P001", session_id="S001", alert_time="2024-01-01T00:00:00",
            severity="critical", anomaly_score=0.95, explanation="test",
        )
        assert len(alert.alert_id) == 16

    def test_from_dict(self) -> None:
        data = {
            "patient_id": "P001", "session_id": "S001", "alert_time": "2024-01-01T00:00:00",
            "severity": "warning", "anomaly_score": 0.7, "explanation": "elevated",
            "alert_id": "abc123",
        }
        alert = AlertEvent.from_dict(data)
        assert alert.alert_id == "abc123"


class TestDLQRecord:
    def test_construction(self) -> None:
        record = DLQRecord(
            original_topic="eeg.raw",
            original_payload={"patient_id": "P001"},
            error_reason="Missing session_id",
        )
        assert record.error_timestamp != ""
        assert record.fingerprint != ""

    def test_from_dict(self) -> None:
        data = {
            "original_topic": "ehr.updates",
            "original_payload": {"patient_id": "P002"},
            "error_reason": "Invalid timestamp",
            "retry_count": 2,
        }
        record = DLQRecord.from_dict(data)
        assert record.retry_count == 2


class TestBronzeRecords:
    def test_bronze_eeg_from_event(self) -> None:
        eeg = EEGChunkEvent(
            patient_id="P001", session_id="S001", event_time="2024-01-15T10:00:00",
            site_id="S0001", channel_count=21, sampling_rate_hz=256.0,
            window_seconds=1.0, source_uri="",
        )
        bronze = BronzeEEGRecord.from_eeg_event(eeg)
        assert bronze.date == "2024-01-15"
        assert bronze.ingestion_time != ""

    def test_bronze_ehr_from_event(self) -> None:
        ehr = EHREvent(
            patient_id="P001", encounter_id="E001", event_time="2024-03-20T08:00:00",
            event_type="admission", source_system="EMR", version=1, payload={},
        )
        bronze = BronzeEHRRecord.from_ehr_event(ehr)
        assert bronze.date == "2024-03-20"


class TestUtilities:
    def test_compute_fingerprint_deterministic(self) -> None:
        data = {"patient_id": "P001", "session_id": "S001"}
        fp1 = compute_fingerprint(data)
        fp2 = compute_fingerprint(data)
        assert fp1 == fp2
        assert len(fp1) == 64  # SHA-256 hex

    def test_fingerprint_ignores_metadata(self) -> None:
        data1 = {"patient_id": "P001", "fingerprint": "old", "ingestion_time": "t1"}
        data2 = {"patient_id": "P001", "fingerprint": "new", "ingestion_time": "t2"}
        assert compute_fingerprint(data1) == compute_fingerprint(data2)

    def test_to_payload(self) -> None:
        event = EEGChunkEvent(
            patient_id="P001", session_id="S001", event_time="2024-01-01T00:00:00",
            site_id="S0001", channel_count=21, sampling_rate_hz=256.0,
            window_seconds=1.0, source_uri="",
        )
        payload = to_payload(event)
        assert isinstance(payload, dict)
        assert payload["patient_id"] == "P001"

    def test_to_json(self) -> None:
        event = EEGChunkEvent(
            patient_id="P001", session_id="S001", event_time="2024-01-01T00:00:00",
            site_id="S0001", channel_count=21, sampling_rate_hz=256.0,
            window_seconds=1.0, source_uri="",
        )
        j = to_json(event)
        parsed = json.loads(j)
        assert parsed["patient_id"] == "P001"

    def test_validate_required_fields_missing(self) -> None:
        payload = {"patient_id": "P001"}
        missing = validate_required_fields(payload, EEG_REQUIRED_FIELDS)
        assert "session_id" in missing
        assert "patient_id" not in missing

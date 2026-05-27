"""Tests for brainwatch.processing.batch_bronze — 8 test cases."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from brainwatch.processing.batch_bronze import load_eeg_jsonl, load_ehr_jsonl


def _make_eeg_jsonl(path: Path, records: list[dict]) -> Path:
    with path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    return path


def _make_ehr_jsonl(path: Path, records: list[dict]) -> Path:
    with path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    return path


class TestBronzeEEGLoader:
    def test_load_valid_records(self, tmp_path: Path) -> None:
        jsonl = _make_eeg_jsonl(tmp_path / "eeg.jsonl", [
            {"patient_id": "P001", "session_id": "S001", "event_time": "2024-01-01T00:00:00",
             "site_id": "S0001", "channel_count": 21, "sampling_rate_hz": 256.0,
             "window_seconds": 1.0, "source_uri": ""},
        ])
        records = load_eeg_jsonl(jsonl)
        assert len(records) == 1
        assert records[0]["patient_id"] == "P001"

    def test_deduplication(self, tmp_path: Path) -> None:
        record = {"patient_id": "P001", "session_id": "S001", "event_time": "2024-01-01T00:00:00",
                  "site_id": "S0001", "channel_count": 21, "sampling_rate_hz": 256.0,
                  "window_seconds": 1.0, "source_uri": ""}
        jsonl = _make_eeg_jsonl(tmp_path / "eeg.jsonl", [record, record])  # duplicate
        records = load_eeg_jsonl(jsonl)
        assert len(records) == 1

    def test_invalid_records_skipped(self, tmp_path: Path) -> None:
        jsonl = _make_eeg_jsonl(tmp_path / "eeg.jsonl", [
            {"patient_id": "", "session_id": "", "event_time": ""},  # invalid
            {"patient_id": "P001", "session_id": "S001", "event_time": "2024-01-01T00:00:00",
             "site_id": "S0001", "channel_count": 21, "sampling_rate_hz": 256.0,
             "window_seconds": 1.0, "source_uri": ""},
        ])
        records = load_eeg_jsonl(jsonl)
        assert len(records) == 1

    def test_empty_file(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "empty.jsonl"
        jsonl.write_text("", encoding="utf-8")
        records = load_eeg_jsonl(jsonl)
        assert records == []

    def test_wrapped_format(self, tmp_path: Path) -> None:
        wrapped = {"key": "P001", "value": {
            "patient_id": "P001", "session_id": "S001", "event_time": "2024-01-01T00:00:00",
            "site_id": "S0001", "channel_count": 21, "sampling_rate_hz": 256.0,
            "window_seconds": 1.0, "source_uri": "",
        }}
        jsonl = _make_eeg_jsonl(tmp_path / "eeg.jsonl", [wrapped])
        records = load_eeg_jsonl(jsonl)
        assert len(records) == 1


class TestBronzeEHRLoader:
    def test_load_valid_ehr(self, tmp_path: Path) -> None:
        jsonl = _make_ehr_jsonl(tmp_path / "ehr.jsonl", [
            {"patient_id": "P001", "encounter_id": "E001", "event_time": "2024-01-01T00:00:00",
             "event_type": "admission", "source_system": "EMR", "version": 1, "payload": {}},
        ])
        records = load_ehr_jsonl(jsonl)
        assert len(records) == 1

    def test_ehr_deduplication(self, tmp_path: Path) -> None:
        record = {"patient_id": "P001", "encounter_id": "E001", "event_time": "2024-01-01T00:00:00",
                  "event_type": "admission", "source_system": "EMR", "version": 1, "payload": {}}
        jsonl = _make_ehr_jsonl(tmp_path / "ehr.jsonl", [record, record])
        records = load_ehr_jsonl(jsonl)
        assert len(records) == 1

    def test_ehr_invalid_skipped(self, tmp_path: Path) -> None:
        jsonl = _make_ehr_jsonl(tmp_path / "ehr.jsonl", [
            {"patient_id": "", "encounter_id": "", "event_time": ""},
        ])
        records = load_ehr_jsonl(jsonl)
        assert len(records) == 0

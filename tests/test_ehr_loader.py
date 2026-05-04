"""Unit tests for EHR loader module."""

import json
import tempfile
from pathlib import Path

import pytest

from brainwatch.ingestion.ehr_loader import (
    build_ehr_event,
    parse_ehr_timestamp,
    summarize_ehr_metadata,
    validate_ehr_row,
)


def test_parse_ehr_timestamp_valid():
    """Test parsing valid timestamp."""
    result = parse_ehr_timestamp("2024-05-15T10:30:00Z")
    assert result == "2024-05-15T10:30:00Z"


def test_parse_ehr_timestamp_empty():
    """Test parsing empty/None timestamp."""
    assert parse_ehr_timestamp(None) is None
    assert parse_ehr_timestamp("") is None
    assert parse_ehr_timestamp("  ") is None


def test_validate_ehr_row_valid():
    """Test validation of valid EHR record."""
    row = {
        "PatientID": "PAT001",
        "EncounterID": "ENC001",
        "EventTime": "2024-05-15T10:30:00Z",
        "EventType": "LAB_RESULT",
        "SourceSystem": "LAB_SYSTEM",
        "Version": "1",
    }
    is_valid, missing = validate_ehr_row(row)
    assert is_valid is True
    assert missing == []


def test_validate_ehr_row_missing_fields():
    """Test validation detects missing required fields."""
    row = {
        "PatientID": "PAT001",
        "EncounterID": "ENC001",
        # Missing EventTime, EventType, SourceSystem
    }
    is_valid, missing = validate_ehr_row(row)
    assert is_valid is False
    assert "EventTime" in missing
    assert "EventType" in missing


def test_build_ehr_event_valid():
    """Test building EHREvent from valid row."""
    row = {
        "PatientID": "PAT001",
        "EncounterID": "ENC001",
        "EventTime": "2024-05-15T10:30:00Z",
        "EventType": "LAB_RESULT",
        "SourceSystem": "LAB_SYSTEM",
        "Version": "2",
        "TestName": "Blood Glucose",
        "Result": "105",
    }
    event = build_ehr_event(row)
    assert event is not None
    assert event.patient_id == "PAT001"
    assert event.encounter_id == "ENC001"
    assert event.event_type == "LAB_RESULT"
    assert event.version == 2
    assert event.payload["TestName"] == "Blood Glucose"


def test_build_ehr_event_invalid():
    """Test building EHREvent from invalid row."""
    row = {
        "PatientID": "PAT001",
        # Missing other required fields
    }
    event = build_ehr_event(row)
    assert event is None


def test_summarize_ehr_metadata():
    """Test summarization of EHR metadata from CSV."""
    csv_content = """PatientID,EncounterID,EventTime,EventType,SourceSystem,Version,TestName
PAT001,ENC001,2024-05-15T10:00:00Z,LAB_RESULT,LAB_SYSTEM,1,Blood Glucose
PAT001,ENC002,2024-05-15T11:00:00Z,VITAL_SIGN,VITAL_SYSTEM,1,BP
PAT002,ENC003,2024-05-15T12:00:00Z,LAB_RESULT,LAB_SYSTEM,1,Creatinine
PAT002,ENC003,,LAB_RESULT,LAB_SYSTEM,1,Invalid Row
"""
    
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = Path(tmpdir) / "ehr_sample.csv"
        csv_path.write_text(csv_content)
        
        summary = summarize_ehr_metadata([csv_path])
        
        assert summary["total_records"] == 4
        assert summary["valid_records"] == 3
        assert summary["invalid_records"] == 1
        assert summary["unique_patients"] == 2
        assert summary["unique_encounters"] == 3
        assert summary["event_type_distribution"]["LAB_RESULT"] == 2
        assert summary["event_type_distribution"]["VITAL_SIGN"] == 1

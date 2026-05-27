"""Tests for brainwatch.ingestion.ehr_generator — 8 test cases."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from brainwatch.ingestion.ehr_generator import (
    EHR_EVENT_TYPES,
    NEUROLOGY_ICD10_CODES,
    generate_synthetic_ehr_events,
    write_ehr_events_jsonl,
)


class TestSyntheticEHRGeneration:
    def test_generates_correct_count(self) -> None:
        events = generate_synthetic_ehr_events(
            patient_ids=["P001", "P002"], events_per_patient=3, seed=42,
        )
        assert len(events) == 6

    def test_all_events_have_patient_id(self) -> None:
        events = generate_synthetic_ehr_events(
            patient_ids=["P001"], events_per_patient=5, seed=42,
        )
        assert all(e.patient_id == "P001" for e in events)

    def test_events_have_valid_event_types(self) -> None:
        events = generate_synthetic_ehr_events(
            patient_ids=["P001"], events_per_patient=7, seed=42,
        )
        for event in events:
            assert event.event_type in EHR_EVENT_TYPES

    def test_seed_produces_deterministic_results(self) -> None:
        events1 = generate_synthetic_ehr_events(["P001"], 3, seed=42)
        events2 = generate_synthetic_ehr_events(["P001"], 3, seed=42)
        assert [e.event_type for e in events1] == [e.event_type for e in events2]

    def test_events_have_payload(self) -> None:
        events = generate_synthetic_ehr_events(["P001"], 5, seed=42)
        assert all(isinstance(e.payload, dict) for e in events)
        assert all(len(e.payload) > 0 for e in events)

    def test_events_have_encounter_id(self) -> None:
        events = generate_synthetic_ehr_events(["P001"], 3, seed=42)
        assert all(e.encounter_id.startswith("ENC-") for e in events)

    def test_empty_patient_list(self) -> None:
        events = generate_synthetic_ehr_events([], 5, seed=42)
        assert events == []

    def test_write_jsonl(self, tmp_path: Path) -> None:
        events = generate_synthetic_ehr_events(["P001", "P002"], 2, seed=42)
        output_path = tmp_path / "ehr.jsonl"
        write_ehr_events_jsonl(events, output_path)

        assert output_path.exists()
        lines = output_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 4

        parsed = json.loads(lines[0])
        assert "patient_id" in parsed
        assert "event_type" in parsed

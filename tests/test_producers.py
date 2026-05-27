"""Tests for brainwatch.ingestion.producers — 12 test cases."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from brainwatch.contracts.events import EEGChunkEvent, EHREvent
from brainwatch.ingestion.dlq import DeadLetterQueue
from brainwatch.ingestion.producers import EEGProducer, EHRProducer
from brainwatch.ingestion.writers import FileEventWriter


@pytest.fixture
def file_writer(tmp_path: Path) -> FileEventWriter:
    return FileEventWriter(tmp_path / "output")


@pytest.fixture
def dlq(tmp_path: Path) -> DeadLetterQueue:
    return DeadLetterQueue(output_path=tmp_path / "dlq.jsonl")


class TestEEGProducer:
    def test_produce_single_event(self, file_writer: FileEventWriter) -> None:
        producer = EEGProducer(writer=file_writer, topic="eeg.raw")
        event = EEGChunkEvent(
            patient_id="P001", session_id="S001", event_time="2024-01-01T00:00:00",
            site_id="S0001", channel_count=21, sampling_rate_hz=256.0,
            window_seconds=1.0, source_uri="test.edf",
        )
        stats = producer.produce_events([event])
        assert stats["produced"] == 1
        assert stats["failed"] == 0

    def test_produce_multiple_events(self, file_writer: FileEventWriter) -> None:
        producer = EEGProducer(writer=file_writer, topic="eeg.raw")
        events = [
            EEGChunkEvent(
                patient_id=f"P{i:03d}", session_id=f"S{i:03d}",
                event_time="2024-01-01T00:00:00", site_id="S0001",
                channel_count=21, sampling_rate_hz=256.0,
                window_seconds=1.0, source_uri="",
            )
            for i in range(5)
        ]
        stats = producer.produce_events(events)
        assert stats["produced"] == 5

    def test_invalid_event_goes_to_dlq(self, file_writer: FileEventWriter, dlq: DeadLetterQueue) -> None:
        producer = EEGProducer(writer=file_writer, topic="eeg.raw", dlq=dlq)
        # Missing required fields
        event = EEGChunkEvent(
            patient_id="", session_id="", event_time="",
            site_id="", channel_count=0, sampling_rate_hz=0.0,
            window_seconds=0.0, source_uri="",
        )
        stats = producer.produce_events([event])
        assert stats["failed"] == 1
        assert dlq.count() == 1

    def test_produce_from_manifest(self, file_writer: FileEventWriter, tmp_path: Path) -> None:
        manifest = {
            "record_count": 1,
            "estimated_total_hours": 0.1,
            "records": [
                {
                    "site_id": "S0001", "subject_id": "sub-001",
                    "session_id": "1", "duration_seconds": 10.0,
                    "candidate_source_keys": ["key1"],
                }
            ],
        }
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        producer = EEGProducer(writer=file_writer, topic="eeg.raw", chunk_duration_sec=5.0)
        stats = producer.produce_from_manifest(manifest_path)
        assert stats["produced"] >= 1

    def test_file_writer_creates_output(self, file_writer: FileEventWriter) -> None:
        producer = EEGProducer(writer=file_writer, topic="eeg.raw")
        event = EEGChunkEvent(
            patient_id="P001", session_id="S001", event_time="2024-01-01T00:00:00",
            site_id="S0001", channel_count=21, sampling_rate_hz=256.0,
            window_seconds=1.0, source_uri="",
        )
        producer.produce_events([event])
        file_writer.flush()
        assert file_writer.counts.get("eeg_raw", 0) >= 1

    def test_chunk_duration_affects_event_count(self, file_writer: FileEventWriter, tmp_path: Path) -> None:
        manifest = {
            "records": [{"site_id": "S0001", "subject_id": "sub-001",
                         "session_id": "1", "duration_seconds": 10.0, "candidate_source_keys": []}],
        }
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        p1 = EEGProducer(writer=file_writer, topic="eeg.raw", chunk_duration_sec=1.0)
        s1 = p1.produce_from_manifest(manifest_path)

        p2_writer = FileEventWriter(tmp_path / "output2")
        p2 = EEGProducer(writer=p2_writer, topic="eeg.raw", chunk_duration_sec=5.0)
        s2 = p2.produce_from_manifest(manifest_path)

        assert s1["produced"] >= s2["produced"]


class TestEHRProducer:
    def test_produce_single_event(self, file_writer: FileEventWriter) -> None:
        producer = EHRProducer(writer=file_writer, topic="ehr.updates")
        event = EHREvent(
            patient_id="P001", encounter_id="E001", event_time="2024-01-01T00:00:00",
            event_type="admission", source_system="EMR-A", version=1, payload={},
        )
        stats = producer.produce_events([event])
        assert stats["produced"] == 1

    def test_invalid_ehr_event(self, file_writer: FileEventWriter, dlq: DeadLetterQueue) -> None:
        producer = EHRProducer(writer=file_writer, topic="ehr.updates", dlq=dlq)
        event = EHREvent(
            patient_id="", encounter_id="", event_time="",
            event_type="invalid", source_system="", version=0, payload={},
        )
        stats = producer.produce_events([event])
        assert stats["failed"] == 1

    def test_produce_from_jsonl(self, file_writer: FileEventWriter, tmp_path: Path) -> None:
        jsonl_path = tmp_path / "ehr.jsonl"
        events_data = [
            {
                "patient_id": "P001", "encounter_id": "E001",
                "event_time": "2024-01-01T00:00:00", "event_type": "admission",
                "source_system": "EMR-A", "version": 1, "payload": {},
            },
            {
                "patient_id": "P002", "encounter_id": "E002",
                "event_time": "2024-01-02T00:00:00", "event_type": "lab_result",
                "source_system": "LAB", "version": 1, "payload": {"test": "CBC"},
            },
        ]
        jsonl_path.write_text(
            "\n".join(json.dumps(e) for e in events_data),
            encoding="utf-8",
        )

        producer = EHRProducer(writer=file_writer, topic="ehr.updates")
        stats = producer.produce_from_jsonl(jsonl_path)
        assert stats["produced"] == 2

    def test_ehr_producer_with_dlq(self, file_writer: FileEventWriter, dlq: DeadLetterQueue) -> None:
        producer = EHRProducer(writer=file_writer, topic="ehr.updates", dlq=dlq)
        # Mix of valid and invalid
        events = [
            EHREvent(
                patient_id="P001", encounter_id="E001",
                event_time="2024-01-01T00:00:00", event_type="admission",
                source_system="EMR", version=1, payload={},
            ),
            EHREvent(
                patient_id="", encounter_id="",
                event_time="", event_type="bad",
                source_system="", version=0, payload={},
            ),
        ]
        stats = producer.produce_events(events)
        assert stats["produced"] == 1
        assert stats["failed"] == 1

    def test_ehr_producer_empty_list(self, file_writer: FileEventWriter) -> None:
        producer = EHRProducer(writer=file_writer, topic="ehr.updates")
        stats = producer.produce_events([])
        assert stats["produced"] == 0
        assert stats["failed"] == 0

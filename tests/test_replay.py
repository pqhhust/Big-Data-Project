"""Tests for replay_to_kafka functionality — 4 test cases."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from brainwatch.ingestion.producers import EEGProducer
from brainwatch.ingestion.writers import FileEventWriter


class TestReplayFunctionality:
    def test_replay_manifest_to_file(self, tmp_path: Path) -> None:
        manifest = {
            "records": [
                {"site_id": "S0001", "subject_id": "sub-001", "session_id": "1",
                 "duration_seconds": 5.0, "candidate_source_keys": ["key1"]},
            ],
        }
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        writer = FileEventWriter(tmp_path / "output")
        producer = EEGProducer(writer=writer, topic="eeg.raw", chunk_duration_sec=5.0)
        stats = producer.produce_from_manifest(manifest_path)
        writer.close()

        assert stats["produced"] >= 1
        assert (tmp_path / "output" / "eeg_raw.jsonl").exists()

    def test_replay_empty_manifest(self, tmp_path: Path) -> None:
        manifest = {"records": []}
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        writer = FileEventWriter(tmp_path / "output")
        producer = EEGProducer(writer=writer, topic="eeg.raw")
        stats = producer.produce_from_manifest(manifest_path)
        assert stats["produced"] == 0

    def test_replay_multiple_records(self, tmp_path: Path) -> None:
        manifest = {
            "records": [
                {"site_id": "S0001", "subject_id": f"sub-{i:03d}", "session_id": str(i),
                 "duration_seconds": 3.0, "candidate_source_keys": []}
                for i in range(3)
            ],
        }
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        writer = FileEventWriter(tmp_path / "output")
        producer = EEGProducer(writer=writer, topic="eeg.raw", chunk_duration_sec=3.0)
        stats = producer.produce_from_manifest(manifest_path)
        assert stats["produced"] >= 3

    def test_fallback_mode_creates_jsonl(self, tmp_path: Path) -> None:
        writer = FileEventWriter(tmp_path / "fallback")
        writer.write("eeg.raw", "P001", '{"patient_id": "P001"}')
        writer.write("ehr.updates", "P001", '{"patient_id": "P001"}')
        writer.flush()
        writer.close()

        assert (tmp_path / "fallback" / "eeg_raw.jsonl").exists()
        assert (tmp_path / "fallback" / "ehr_updates.jsonl").exists()

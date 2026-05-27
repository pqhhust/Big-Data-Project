"""Tests for brainwatch.ingestion.writers — 8 test cases."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from brainwatch.ingestion.writers import DualWriter, FileEventWriter


class TestFileEventWriter:
    def test_write_creates_file(self, tmp_path: Path) -> None:
        writer = FileEventWriter(tmp_path)
        writer.write("eeg.raw", "P001", '{"patient_id": "P001"}')
        writer.flush()
        assert (tmp_path / "eeg_raw.jsonl").exists()

    def test_write_appends_multiple_records(self, tmp_path: Path) -> None:
        writer = FileEventWriter(tmp_path)
        writer.write("eeg.raw", "P001", '{"patient_id": "P001"}')
        writer.write("eeg.raw", "P002", '{"patient_id": "P002"}')
        writer.flush()

        lines = (tmp_path / "eeg_raw.jsonl").read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2

    def test_write_different_topics(self, tmp_path: Path) -> None:
        writer = FileEventWriter(tmp_path)
        writer.write("eeg.raw", "P001", '{"type": "eeg"}')
        writer.write("ehr.updates", "P001", '{"type": "ehr"}')
        writer.flush()
        assert (tmp_path / "eeg_raw.jsonl").exists()
        assert (tmp_path / "ehr_updates.jsonl").exists()

    def test_counts_per_topic(self, tmp_path: Path) -> None:
        writer = FileEventWriter(tmp_path)
        writer.write("eeg.raw", "P001", '{"a": 1}')
        writer.write("eeg.raw", "P002", '{"a": 2}')
        writer.write("ehr.updates", "P001", '{"b": 1}')
        assert writer.counts["eeg_raw"] == 2
        assert writer.counts["ehr_updates"] == 1

    def test_close_releases_handles(self, tmp_path: Path) -> None:
        writer = FileEventWriter(tmp_path)
        writer.write("test.topic", "key", '{"x": 1}')
        writer.close()
        assert len(writer._handles) == 0

    def test_output_is_valid_json(self, tmp_path: Path) -> None:
        writer = FileEventWriter(tmp_path)
        writer.write("test.topic", "key1", '{"patient_id": "P001", "score": 0.5}')
        writer.flush()

        content = (tmp_path / "test_topic.jsonl").read_text(encoding="utf-8").strip()
        parsed = json.loads(content)
        assert parsed["key"] == "key1"
        assert parsed["value"]["patient_id"] == "P001"


class TestDualWriter:
    def test_dual_writer_writes_to_both(self, tmp_path: Path) -> None:
        primary = FileEventWriter(tmp_path / "primary")
        secondary = FileEventWriter(tmp_path / "secondary")
        dual = DualWriter(primary, secondary)

        dual.write("eeg.raw", "P001", '{"test": true}')
        dual.flush()

        assert (tmp_path / "primary" / "eeg_raw.jsonl").exists()
        assert (tmp_path / "secondary" / "eeg_raw.jsonl").exists()

    def test_dual_writer_close(self, tmp_path: Path) -> None:
        primary = FileEventWriter(tmp_path / "p")
        secondary = FileEventWriter(tmp_path / "s")
        dual = DualWriter(primary, secondary)
        dual.write("test.t", "k", '{}')
        dual.close()
        assert len(primary._handles) == 0
        assert len(secondary._handles) == 0

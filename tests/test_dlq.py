"""Tests for brainwatch.ingestion.dlq — 8 test cases."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from brainwatch.ingestion.dlq import DeadLetterQueue


class TestDeadLetterQueue:
    def test_send_creates_record(self, tmp_path: Path) -> None:
        dlq = DeadLetterQueue(output_path=tmp_path / "dlq.jsonl")
        record = dlq.send("eeg.raw", {"patient_id": "P001"}, "Missing session_id")
        assert record.original_topic == "eeg.raw"
        assert record.error_reason == "Missing session_id"

    def test_count_increments(self, tmp_path: Path) -> None:
        dlq = DeadLetterQueue(output_path=tmp_path / "dlq.jsonl")
        assert dlq.count() == 0
        dlq.send("t1", {}, "err1")
        dlq.send("t2", {}, "err2")
        assert dlq.count() == 2

    def test_get_records(self, tmp_path: Path) -> None:
        dlq = DeadLetterQueue(output_path=tmp_path / "dlq.jsonl")
        dlq.send("topic", {"id": 1}, "reason")
        records = dlq.get_records()
        assert len(records) == 1
        assert records[0].original_payload["id"] == 1

    def test_file_persistence(self, tmp_path: Path) -> None:
        dlq_path = tmp_path / "dlq.jsonl"
        dlq = DeadLetterQueue(output_path=dlq_path)
        dlq.send("eeg.raw", {"patient_id": "P001"}, "test error")
        dlq.close()

        assert dlq_path.exists()
        content = dlq_path.read_text(encoding="utf-8").strip()
        record = json.loads(content)
        assert record["error_reason"] == "test error"

    def test_replay_filters_max_retries(self, tmp_path: Path) -> None:
        dlq = DeadLetterQueue(output_path=tmp_path / "dlq.jsonl", max_retries=2)
        dlq.send("t", {"id": 1}, "err", retry_count=0)
        dlq.send("t", {"id": 2}, "err", retry_count=2)  # at max
        dlq.send("t", {"id": 3}, "err", retry_count=3)  # over max

        replayable = dlq.replay()
        assert len(replayable) == 1
        assert replayable[0]["id"] == 1

    def test_load_from_file(self, tmp_path: Path) -> None:
        dlq_path = tmp_path / "dlq.jsonl"
        dlq = DeadLetterQueue(output_path=dlq_path)
        dlq.send("topic1", {"a": 1}, "error1")
        dlq.send("topic2", {"b": 2}, "error2")
        dlq.close()

        loaded = dlq.load_from_file(dlq_path)
        assert len(loaded) == 2
        assert loaded[0].original_topic == "topic1"

    def test_send_without_file(self) -> None:
        dlq = DeadLetterQueue()  # no output path
        record = dlq.send("topic", {"key": "val"}, "err")
        assert record is not None
        assert dlq.count() == 1

    def test_fingerprint_set_on_send(self, tmp_path: Path) -> None:
        dlq = DeadLetterQueue(output_path=tmp_path / "dlq.jsonl")
        record = dlq.send("t", {"patient_id": "P001"}, "err")
        assert record.fingerprint != ""
        assert len(record.fingerprint) == 64

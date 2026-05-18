"""Tests for ``brainwatch.ingestion.dead_letter``.

Owner: **Trang**.  Covers Dat's ``DeadLetterQueue``.
"""
from __future__ import annotations

import json
from pathlib import Path

from brainwatch.ingestion.dead_letter import DeadLetterQueue


def test_route_appends_jsonl_envelope(tmp_path: Path):
    """Route a payload, assert the daily file contains the envelope."""
    dlq = DeadLetterQueue(tmp_path)

    payload = {"test": "data", "patient_id": "P001"}
    dlq.route(payload, "test reason")

    assert dlq.count == 1

    # Check file exists
    files = list(tmp_path.glob("dead_letter_*.jsonl"))
    assert len(files) == 1

    # Verify content
    content = json.loads(files[0].read_text().strip())
    assert content["reason"] == "test reason"
    assert content["original_payload"]["patient_id"] == "P001"
    assert "routed_at" in content


def test_count_increments(tmp_path: Path):
    """Route 3 records, assert dlq.count == 3."""
    dlq = DeadLetterQueue(tmp_path)

    dlq.route({"id": 1}, "reason 1")
    dlq.route({"id": 2}, "reason 2")
    dlq.route({"id": 3}, "reason 3")

    assert dlq.count == 3


def test_read_all_returns_records_in_order(tmp_path: Path):
    """Route 5 payloads, read_all returns them in order."""
    dlq = DeadLetterQueue(tmp_path)

    for i in range(5):
        dlq.route({"id": i}, f"reason {i}")

    records = dlq.read_all()
    assert len(records) == 5

    # Verify order matches routing order
    for i, record in enumerate(records):
        assert record["original_payload"]["id"] == i
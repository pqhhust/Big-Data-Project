"""Tests for ``brainwatch.ingestion.dead_letter``.

Owner: **Trang**.  Covers Dat's ``DeadLetterQueue``.
"""
from __future__ import annotations

import json
from pathlib import Path


def test_route_appends_jsonl_envelope(tmp_path: Path):
    """Trang: route a payload, assert the daily file contains one record with
    the keys ``routed_at``, ``reason``, ``original_payload``."""
    pass


def test_count_increments(tmp_path: Path):
    """Trang: route 3 records, assert dlq.count == 3."""
    pass


def test_read_all_returns_records_in_order(tmp_path: Path):
    """Trang: route 5 distinct payloads, call read_all, assert the order of
    ``original_payload`` values matches the order they were routed in."""
    pass

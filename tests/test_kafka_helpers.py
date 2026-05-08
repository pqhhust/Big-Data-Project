"""Tests for ``brainwatch.ingestion.kafka_helpers``.

Owner: **Trang**.  Covers Kim-Hung's serialisation helpers + ``FileProducer``.

These tests must pass without ``kafka-python`` installed — that's the whole
point of the FileProducer fallback. Do NOT add ``import kafka`` at module
top-level.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

# Trang: once Kim-Hung lands the module, uncomment:
#   from brainwatch.ingestion.kafka_helpers import (
#       event_to_bytes, bytes_to_dict, FileProducer, get_producer,
#   )
#   from brainwatch.contracts.events import EEGChunkEvent


def test_event_to_bytes_roundtrip():
    """Trang: build an EEGChunkEvent, serialise → deserialise, assert equality
    on every field. ~5 lines."""
    # Trang: code the roundtrip assertion here.
    pass


def test_file_producer_appends_jsonl(tmp_path: Path):
    """Trang: FileProducer.send writes one JSONL line per call, each with the
    shape ``{"topic": ..., "value": ...}``. Send 3 events, read back the file,
    assert 3 lines and the topic/value fields."""
    # Trang: code the FileProducer test here.
    pass


def test_get_producer_falls_back_when_no_kafka(tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch):
    """Trang: monkey-patch ``create_producer`` to raise, call get_producer with
    a fallback path, assert the returned object is a FileProducer (or at least
    has ``.send``, ``.flush``, ``.close``)."""
    # Trang: code the fallback test here.
    pass

"""Tests for ``brainwatch.ingestion.kafka_helpers``.

Owner: **Trang**.  Covers Kim-Hung's serialisation helpers + ``FileProducer``.

These tests must pass without ``kafka-python`` installed — that's the whole
point of the FileProducer fallback.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from brainwatch.ingestion.kafka_helpers import (
    event_to_bytes, bytes_to_dict, FileProducer, get_producer,
)
from brainwatch.contracts.events import EEGChunkEvent


def test_event_to_bytes_roundtrip():
    """Serialise an EEGChunkEvent and verify roundtrip."""
    event = EEGChunkEvent(
        patient_id="P001",
        session_id="S001",
        event_time="2026-05-19T10:00:00Z",
        site_id="SITE01",
        channel_count=19,
        sampling_rate_hz=200.0,
        window_seconds=30.0,
        source_uri="s3://bucket/file.edf"
    )

    serialized = event_to_bytes(event)
    assert isinstance(serialized, bytes)

    deserialized = bytes_to_dict(serialized)
    assert deserialized["patient_id"] == "P001"
    assert deserialized["session_id"] == "S001"
    assert deserialized["site_id"] == "SITE01"
    assert deserialized["channel_count"] == 19
    assert deserialized["sampling_rate_hz"] == 200.0


def test_file_producer_appends_jsonl(tmp_path: Path):
    """FileProducer sends one JSONL line per call."""
    output_path = tmp_path / "test_fallback.jsonl"
    producer = FileProducer(str(output_path))

    producer.send("eeg.raw", {"patient_id": "P001", "session_id": "S001"})
    producer.send("eeg.raw", {"patient_id": "P002", "session_id": "S002"})
    producer.send("ehr.updates", {"patient_id": "P001", "event_type": "vital_signs"})
    producer.close()

    content = output_path.read_text().strip().split("\n")
    assert len(content) == 3

    line1 = json.loads(content[0])
    assert line1["topic"] == "eeg.raw"
    assert line1["value"]["patient_id"] == "P001"

    line3 = json.loads(content[2])
    assert line3["topic"] == "ehr.updates"


def test_get_producer_falls_back_when_no_kafka(tmp_path: Path):
    """get_producer falls back to FileProducer when Kafka is unavailable."""
    fallback_path = tmp_path / "fallback.jsonl"

    # Force fallback by providing invalid bootstrap servers
    producer = get_producer(bootstrap_servers="invalid:9999", fallback_path=str(fallback_path))

    # Should be a FileProducer
    assert hasattr(producer, "send")
    assert hasattr(producer, "flush")
    assert hasattr(producer, "close")

    producer.send("test.topic", {"test": "data"})
    producer.close()

    assert fallback_path.exists()
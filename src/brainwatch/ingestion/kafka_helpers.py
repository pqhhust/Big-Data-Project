"""Kafka connection helpers and serialisation utilities.

Owner: **Kim-Hung**.
Consumed by: ``eeg_producer.py``, ``ehr_normalizer.py`` (Kim-Quan), and
``scripts/replay_to_kafka.py`` (Quang-Hung).

Hard contract: every public function in this file must work whether or not
``kafka-python`` is installed. The ``FileProducer`` fallback is what keeps the
test suite green on a fresh clone.
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------

def event_to_bytes(event: Any) -> bytes:
    """Serialise a dataclass event (or dict) to UTF-8 JSON bytes."""
    from dataclasses import asdict
    if hasattr(event, '__dataclass_fields__'):
        payload = asdict(event)
    else:
        payload = event
    return json.dumps(payload, default=str).encode("utf-8")


def bytes_to_dict(raw: bytes) -> dict[str, Any]:
    """Inverse of :func:`event_to_bytes`."""
    return json.loads(raw.decode("utf-8"))


# ---------------------------------------------------------------------------
# Producer factory
# ---------------------------------------------------------------------------

def create_producer(bootstrap_servers: str = "localhost:9092", **kwargs: Any):
    """Return a real ``KafkaProducer``. Raises ``ImportError`` if ``kafka-python``
    is not installed — that's intentional, callers should use :func:`get_producer`
    if they want graceful fallback."""
    from kafka import KafkaProducer
    return KafkaProducer(
        bootstrap_servers=bootstrap_servers,
        acks="all",
        retries=3,
        value_serializer=lambda v: event_to_bytes(v),
        **kwargs
    )


def create_consumer(topic: str, bootstrap_servers: str = "localhost:9092",
                    group_id: str = "brainwatch", **kwargs: Any):
    """Return a real ``KafkaConsumer``. Same fail-mode as :func:`create_producer`."""
    from kafka import KafkaConsumer
    return KafkaConsumer(
        topic,
        bootstrap_servers=bootstrap_servers,
        group_id=group_id,
        auto_offset_reset="earliest",
        enable_auto_commit=True,
        value_deserializer=lambda m: bytes_to_dict(m),
        **kwargs
    )


# ---------------------------------------------------------------------------
# File-based fallback (no Kafka required)
# ---------------------------------------------------------------------------

class FileProducer:
    """Drop-in replacement that appends JSON-lines to a local file."""

    def __init__(self, output_path: str) -> None:
        from pathlib import Path
        self._output_path = Path(output_path)
        self._output_path.parent.mkdir(parents=True, exist_ok=True)

    def send(self, topic: str, value: Any, **_kwargs: Any) -> None:
        from dataclasses import asdict
        record = {"topic": topic, "value": value}
        if hasattr(value, '__dataclass_fields__'):
            record["value"] = asdict(value)
        with self._output_path.open("a") as f:
            f.write(json.dumps(record, default=str) + "\n")

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass


def get_producer(bootstrap_servers: str = "localhost:9092",
                 fallback_path: str | None = None, **kwargs: Any):
    """Try real Kafka; fall back to :class:`FileProducer` on any failure."""
    try:
        return create_producer(bootstrap_servers, **kwargs)
    except Exception as e:
        logger.warning(f"Kafka unavailable ({e}), falling back to file producer")
        return FileProducer(fallback_path or "artifacts/week2/kafka_fallback.jsonl")
"""Kafka connection helpers and serialisation utilities.

Owner: **Kim-Hung**.
Consumed by: ``eeg_producer.py``, ``ehr_normalizer.py`` (Kim-Quan), and
``scripts/replay_to_kafka.py`` (Quang-Hung).

Hard contract: every public function in this file must work whether or not
``kafka-python`` is installed. The ``FileProducer`` fallback is what keeps the
test suite green on a fresh clone.
"""
from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------

def event_to_bytes(event: Any) -> bytes:
    """Serialise a dataclass event (or dict) to UTF-8 JSON bytes."""
    import json
    from dataclasses import is_dataclass, asdict
    payload = asdict(event) if is_dataclass(event) else event
    return json.dumps(payload, default=str).encode("utf-8")


def bytes_to_dict(raw: bytes) -> dict[str, Any]:
    """Inverse of :func:`event_to_bytes`."""
    import json
    return json.loads(raw.decode("utf-8"))


# ---------------------------------------------------------------------------
# Producer factory
# ---------------------------------------------------------------------------

def create_producer(bootstrap_servers: str = "localhost:9092", **kwargs: Any):
    """Return a real ``KafkaProducer``."""
    from kafka import KafkaProducer
    return KafkaProducer(
        bootstrap_servers=bootstrap_servers,
        acks="all",
        retries=3,
        value_serializer=event_to_bytes,
        **kwargs
    )


def create_consumer(topic: str, bootstrap_servers: str = "localhost:9092",
                    group_id: str = "brainwatch", **kwargs: Any):
    """Return a real ``KafkaConsumer``."""
    from kafka import KafkaConsumer
    return KafkaConsumer(
        topic,
        bootstrap_servers=bootstrap_servers,
        group_id=group_id,
        auto_offset_reset="earliest",
        enable_auto_commit=True,
        value_deserializer=bytes_to_dict,
        **kwargs
    )


# ---------------------------------------------------------------------------
# File-based fallback (no Kafka required)
# ---------------------------------------------------------------------------

class FileProducer:
    """Drop-in replacement that appends JSON-lines to a local file."""

    def __init__(self, output_path: str) -> None:
        import os
        self.output_path = output_path
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    def send(self, topic: str, value: Any, **_kwargs: Any) -> None:
        import json
        from dataclasses import is_dataclass, asdict
        payload = asdict(value) if is_dataclass(value) else value
        record = {"topic": topic, "value": payload}
        with open(self.output_path, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass


def get_producer(bootstrap_servers: str = "localhost:9092",
                 fallback_path: str | None = None, **kwargs: Any):
    """Try real Kafka; fall back to :class:`FileProducer` on any failure."""
    try:
        return create_producer(bootstrap_servers=bootstrap_servers, **kwargs)
    except Exception as e:
        import logging
        logging.warning(f"Kafka unavailable, falling back to FileProducer: {e}")
        path = fallback_path or "artifacts/week2/kafka_fallback.jsonl"
        return FileProducer(path)

"""Kafka connection helpers and serialisation utilities."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------

def event_to_bytes(event: Any) -> bytes:
    """Serialise a dataclass event (or dict) to UTF-8 JSON bytes."""
    payload = asdict(event) if hasattr(event, "__dataclass_fields__") else event
    return json.dumps(payload, default=str).encode("utf-8")


def bytes_to_dict(raw: bytes) -> dict[str, Any]:
    return json.loads(raw.decode("utf-8"))


# ---------------------------------------------------------------------------
# Producer factory
# ---------------------------------------------------------------------------

def create_producer(bootstrap_servers: str = "localhost:9092", **kwargs: Any):
    """Return a KafkaProducer; raises ImportError if kafka-python is absent."""
    from kafka import KafkaProducer  # type: ignore[import-untyped]

    return KafkaProducer(
        bootstrap_servers=bootstrap_servers,
        value_serializer=event_to_bytes,
        acks="all",
        retries=3,
        **kwargs,
    )


def create_consumer(
    topic: str,
    bootstrap_servers: str = "localhost:9092",
    group_id: str = "brainwatch",
    **kwargs: Any,
):
    from kafka import KafkaConsumer  # type: ignore[import-untyped]

    return KafkaConsumer(
        topic,
        bootstrap_servers=bootstrap_servers,
        group_id=group_id,
        value_deserializer=bytes_to_dict,
        auto_offset_reset="earliest",
        enable_auto_commit=True,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# File-based fallback (no Kafka required)
# ---------------------------------------------------------------------------

class FileProducer:
    """Drop-in replacement that appends JSON-lines to a local file."""

    def __init__(self, output_path: str) -> None:
        self._path = output_path

    def send(self, topic: str, value: Any, **_kwargs: Any) -> None:
        payload = asdict(value) if hasattr(value, "__dataclass_fields__") else value
        with open(self._path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"topic": topic, "value": payload}, default=str) + "\n")

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass


def get_producer(
    bootstrap_servers: str = "localhost:9092",
    fallback_path: str | None = None,
    **kwargs: Any,
):
    """Try real Kafka; fall back to FileProducer if unavailable."""
    try:
        return create_producer(bootstrap_servers, **kwargs)
    except Exception:
        path = fallback_path or "artifacts/week2/kafka_fallback.jsonl"
        logger.warning("Kafka unavailable — writing to %s", path)
        return FileProducer(path)

"""Event writers — Kafka and file-based backends.

All writers implement the ``EventWriter`` protocol so producers are
decoupled from the transport layer.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class EventWriter(ABC):
    """Abstract base for all event writers."""

    @abstractmethod
    def write(self, topic: str, key: str, value: str) -> None:
        """Write a single event to the given topic."""

    @abstractmethod
    def flush(self) -> None:
        """Ensure all buffered writes are committed."""

    @abstractmethod
    def close(self) -> None:
        """Release resources."""


class FileEventWriter(EventWriter):
    """Writes events as JSONL files — one file per topic.

    Used as a **file fallback** when Kafka is unavailable.
    """

    def __init__(self, output_dir: str | Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._handles: dict[str, Any] = {}
        self._counts: dict[str, int] = {}

    def write(self, topic: str, key: str, value: str) -> None:
        safe_topic = topic.replace(".", "_")
        if safe_topic not in self._handles:
            path = self.output_dir / f"{safe_topic}.jsonl"
            self._handles[safe_topic] = path.open("a", encoding="utf-8")
            self._counts[safe_topic] = 0

        record = {"key": key, "value": json.loads(value) if isinstance(value, str) else value}
        self._handles[safe_topic].write(json.dumps(record, default=str) + "\n")
        self._counts[safe_topic] += 1

    def flush(self) -> None:
        for handle in self._handles.values():
            handle.flush()

    def close(self) -> None:
        for handle in self._handles.values():
            handle.close()
        self._handles.clear()

    @property
    def counts(self) -> dict[str, int]:
        return dict(self._counts)


class KafkaEventWriter(EventWriter):
    """Writes events to Apache Kafka via ``kafka-python``.

    The Kafka producer is created lazily on the first ``write()`` call so
    this module can be imported without Kafka being installed.
    """

    def __init__(
        self,
        bootstrap_servers: str = "localhost:9094",
        acks: str = "all",
        retries: int = 3,
        max_in_flight: int = 1,
        request_timeout_ms: int = 30000,
        linger_ms: int = 10,
        batch_size: int = 16384,
    ) -> None:
        self._bootstrap_servers = bootstrap_servers
        self._acks = acks
        self._retries = retries
        self._max_in_flight = max_in_flight
        self._request_timeout_ms = request_timeout_ms
        self._linger_ms = linger_ms
        self._batch_size = batch_size
        self._producer: Any = None

    def _ensure_producer(self) -> None:
        if self._producer is not None:
            return
        from kafka import KafkaProducer  # type: ignore[import-untyped]

        self._producer = KafkaProducer(
            bootstrap_servers=self._bootstrap_servers.split(","),
            value_serializer=lambda v: v.encode("utf-8") if isinstance(v, str) else v,
            key_serializer=lambda k: k.encode("utf-8") if isinstance(k, str) else k,
            acks=self._acks,
            retries=self._retries,
            max_in_flight_requests_per_connection=self._max_in_flight,
            request_timeout_ms=self._request_timeout_ms,
            linger_ms=self._linger_ms,
            batch_size=self._batch_size,
        )
        logger.info("KafkaProducer connected to %s", self._bootstrap_servers)

    def write(self, topic: str, key: str, value: str) -> None:
        self._ensure_producer()
        future = self._producer.send(topic, key=key, value=value)
        future.get(timeout=30)

    def flush(self) -> None:
        if self._producer is not None:
            self._producer.flush(timeout=30)

    def close(self) -> None:
        if self._producer is not None:
            self._producer.close()
            self._producer = None


class DualWriter(EventWriter):
    """Writes to **both** a primary and secondary writer simultaneously.

    Useful for writing to Kafka + file at the same time for auditability.
    """

    def __init__(self, primary: EventWriter, secondary: EventWriter) -> None:
        self.primary = primary
        self.secondary = secondary

    def write(self, topic: str, key: str, value: str) -> None:
        self.primary.write(topic, key, value)
        self.secondary.write(topic, key, value)

    def flush(self) -> None:
        self.primary.flush()
        self.secondary.flush()

    def close(self) -> None:
        self.primary.close()
        self.secondary.close()

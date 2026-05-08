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
    """Serialise a dataclass event (or dict) to UTF-8 JSON bytes.

    Kim-Hung: implement.  ~3 lines:
      - ``asdict(event)`` if it's a dataclass, else assume dict
      - ``json.dumps(payload, default=str).encode("utf-8")``
    """
    # Kim-Hung: code JSON serialisation here.
    pass


def bytes_to_dict(raw: bytes) -> dict[str, Any]:
    """Inverse of :func:`event_to_bytes`. Kim-Hung: 1 line."""
    # Kim-Hung: code JSON deserialisation here.
    pass


# ---------------------------------------------------------------------------
# Producer factory
# ---------------------------------------------------------------------------

def create_producer(bootstrap_servers: str = "localhost:9092", **kwargs: Any):
    """Return a real ``KafkaProducer``. Raises ``ImportError`` if ``kafka-python``
    is not installed — that's intentional, callers should use :func:`get_producer`
    if they want graceful fallback.

    Kim-Hung: implement with ``acks="all"``, ``retries=3``,
    ``value_serializer=event_to_bytes``.
    """
    # Kim-Hung: code KafkaProducer construction here.
    pass


def create_consumer(topic: str, bootstrap_servers: str = "localhost:9092",
                    group_id: str = "brainwatch", **kwargs: Any):
    """Return a real ``KafkaConsumer``. Same fail-mode as :func:`create_producer`.

    Kim-Hung: implement with ``auto_offset_reset="earliest"``,
    ``enable_auto_commit=True``, ``value_deserializer=bytes_to_dict``.
    """
    # Kim-Hung: code KafkaConsumer construction here.
    pass


# ---------------------------------------------------------------------------
# File-based fallback (no Kafka required)
# ---------------------------------------------------------------------------

class FileProducer:
    """Drop-in replacement that appends JSON-lines to a local file.

    Kim-Hung: implement. Must mirror the subset of ``KafkaProducer`` we use:
      - ``__init__(self, output_path: str)``
      - ``send(self, topic, value, **_kwargs)`` — append ``{"topic": topic, "value": payload}``
        as one JSON line.
      - ``flush(self)`` — no-op (file IO is synchronous).
      - ``close(self)`` — no-op.
    """

    def __init__(self, output_path: str) -> None:
        # Kim-Hung: code init.
        pass

    def send(self, topic: str, value: Any, **_kwargs: Any) -> None:
        # Kim-Hung: code append-line here.
        pass

    def flush(self) -> None:
        # Kim-Hung: real no-op — file IO is synchronous, nothing to flush.
        pass

    def close(self) -> None:
        # Kim-Hung: real no-op — we open/close the file per send().
        pass


def get_producer(bootstrap_servers: str = "localhost:9092",
                 fallback_path: str | None = None, **kwargs: Any):
    """Try real Kafka; fall back to :class:`FileProducer` on any failure.

    Kim-Hung: implement.
      - ``try: return create_producer(...)``
      - ``except Exception:`` log a warning and return
        ``FileProducer(fallback_path or "artifacts/week2/kafka_fallback.jsonl")``.

    Why not just ``ImportError``?  Because a missing broker raises
    ``NoBrokersAvailable``. Catch broadly here — this is the user-facing API.
    """
    # Kim-Hung: code try/except fallback here.
    pass

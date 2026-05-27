"""Dead-letter queue for events that fail validation or processing."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from brainwatch.contracts.events import DLQRecord

logger = logging.getLogger(__name__)


class DeadLetterQueue:
    """Captures failed events and routes them to a DLQ sink.

    Supports file-based and Kafka-based sinks.  In file mode each DLQ record
    is appended to a JSONL file.
    """

    def __init__(
        self,
        output_path: str | Path | None = None,
        kafka_writer: Any = None,
        topic: str = "brainwatch.dlq",
        max_retries: int = 3,
    ) -> None:
        self.output_path = Path(output_path) if output_path else None
        self.kafka_writer = kafka_writer
        self.topic = topic
        self.max_retries = max_retries
        self._records: list[DLQRecord] = []
        self._file_handle: Any = None

        if self.output_path:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send(
        self,
        original_topic: str,
        original_payload: dict[str, Any],
        error_reason: str,
        retry_count: int = 0,
    ) -> DLQRecord:
        """Create and persist a DLQ record for a failed event."""
        record = DLQRecord(
            original_topic=original_topic,
            original_payload=original_payload,
            error_reason=error_reason,
            retry_count=retry_count,
        )
        self._records.append(record)
        self._persist(record)
        logger.warning(
            "DLQ: topic=%s reason=%s fingerprint=%s",
            original_topic,
            error_reason,
            record.fingerprint[:12],
        )
        return record

    def get_records(self) -> list[DLQRecord]:
        """Return all DLQ records captured in this session."""
        return list(self._records)

    def count(self) -> int:
        """Return the number of DLQ records captured."""
        return len(self._records)

    def replay(self) -> list[dict[str, Any]]:
        """Return original payloads for retry, filtering out max-retried records."""
        return [
            r.original_payload
            for r in self._records
            if r.retry_count < self.max_retries
        ]

    def load_from_file(self, path: str | Path | None = None) -> list[DLQRecord]:
        """Load DLQ records from a JSONL file."""
        target = Path(path) if path else self.output_path
        if not target or not target.exists():
            return []

        records: list[DLQRecord] = []
        with target.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                records.append(DLQRecord.from_dict(data))
        return records

    def close(self) -> None:
        """Close file handle if open."""
        if self._file_handle:
            self._file_handle.close()
            self._file_handle = None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _persist(self, record: DLQRecord) -> None:
        """Write the record to the configured sink."""
        if self.output_path:
            self._write_to_file(record)
        if self.kafka_writer:
            self._write_to_kafka(record)

    def _write_to_file(self, record: DLQRecord) -> None:
        if self._file_handle is None:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)  # type: ignore[union-attr]
            self._file_handle = self.output_path.open("a", encoding="utf-8")  # type: ignore[union-attr]
        self._file_handle.write(json.dumps(record.to_dict(), default=str) + "\n")
        self._file_handle.flush()

    def _write_to_kafka(self, record: DLQRecord) -> None:
        try:
            value = json.dumps(record.to_dict(), default=str)
            self.kafka_writer.write(self.topic, record.fingerprint, value)
        except Exception:
            logger.exception("Failed to write DLQ record to Kafka")

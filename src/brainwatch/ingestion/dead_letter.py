"""Dead-letter queue for events that fail validation or processing.

Used by the bronze writer (validation failures) and the S3 download loop
(transfer failures). Appends one JSONL envelope per failed payload.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class DeadLetterQueue:
    """Append-only dead-letter sink backed by a JSONL file."""

    def __init__(self, output_dir: str | Path) -> None:
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._count = 0

    def route(self, payload: dict[str, Any], reason: str) -> None:
        """Wrap the failed payload with metadata and append a line."""
        envelope = {
            "routed_at": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            "original_payload": payload
        }
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        file_path = self._output_dir / f"dead_letter_{today}.jsonl"
        with file_path.open("a") as f:
            f.write(json.dumps(envelope, default=str) + "\n")
        self._count += 1
        logger.warning(f"DLQ routed: {reason}")

    @property
    def count(self) -> int:
        return self._count

    def read_all(self) -> list[dict[str, Any]]:
        """Read back every record across all daily files (for inspection or manual replay)."""
        records = []
        for file_path in sorted(self._output_dir.glob("dead_letter_*.jsonl")):
            with file_path.open() as f:
                for line in f:
                    if line.strip():
                        records.append(json.loads(line))
        return records
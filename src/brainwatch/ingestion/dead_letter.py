"""Dead-letter queue for events that fail validation or processing.

Failed events are written to a JSONL file with error metadata so they can be
inspected, replayed, or discarded during manual review.
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
        self._dir = Path(output_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._count = 0

    def route(self, payload: dict[str, Any], reason: str) -> None:
        """Write a failed event with metadata to the dead-letter file."""
        envelope = {
            "routed_at": datetime.now(tz=timezone.utc).isoformat(),
            "reason": reason,
            "original_payload": payload,
        }
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        dlq_path = self._dir / f"dead_letter_{today}.jsonl"
        with dlq_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(envelope, default=str) + "\n")
        self._count += 1
        logger.warning("DLQ: %s — %s", reason, _truncate(payload))

    @property
    def count(self) -> int:
        return self._count

    def read_all(self) -> list[dict[str, Any]]:
        """Read back all dead-letter records (for inspection / replay)."""
        records: list[dict[str, Any]] = []
        for path in sorted(self._dir.glob("dead_letter_*.jsonl")):
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))
        return records


def _truncate(d: dict, max_len: int = 120) -> str:
    s = json.dumps(d, default=str)
    return s if len(s) <= max_len else s[:max_len] + "..."

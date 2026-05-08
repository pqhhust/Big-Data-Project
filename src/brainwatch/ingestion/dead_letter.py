"""Dead-letter queue for events that fail validation or processing.

Owner: **Dat**.
Used by: Kim-Hung's ``BronzeWriter``, Kim-Hung's ``download_subset`` (on S3
failures), and possibly Quang-Hung's Spark stream (parallel DLQ topic).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


class DeadLetterQueue:
    """Append-only dead-letter sink backed by a JSONL file.

    Dat: implement. One file per UTC day, named ``dead_letter_YYYY-MM-DD.jsonl``,
    inside ``output_dir``.
    """

    def __init__(self, output_dir: str | Path) -> None:
        # Dat: code init.
        #   - mkdir parents=True for output_dir
        #   - init self._count = 0
        pass

    def route(self, payload: dict[str, Any], reason: str) -> None:
        """Wrap the failed payload with metadata and append a line.

        Envelope shape::

            {"routed_at": "<ISO-8601 UTC now>",
             "reason": "<short reason>",
             "original_payload": <payload>}

        Dat: implement. Bump ``self._count`` on each call. Log a warning
        (use ``logging.getLogger(__name__).warning(...)``) so the operator
        sees something in the console.
        """
        # Dat: code envelope build + append-line here.
        pass

    @property
    def count(self) -> int:
        # Dat: return self._count.
        pass

    def read_all(self) -> list[dict[str, Any]]:
        """Read back every record across all daily files (for inspection or
        manual replay). Sort files by name so output is deterministic.

        Dat: implement.
        """
        # Dat: code read-all here.
        pass

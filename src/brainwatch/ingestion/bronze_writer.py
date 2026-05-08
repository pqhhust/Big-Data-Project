"""Bronze-zone writer with deduplication and DLQ routing.

Owner: **Kim-Hung**.
Depends on: ``brainwatch.contracts.events`` (already exists), and
``brainwatch.ingestion.dead_letter.DeadLetterQueue`` (Dat).

Bronze layout on disk::

    data/lake/bronze/
    ├── eeg/
    │   └── site=S0001/
    │       └── date=2026-04-19/
    │           └── eeg_bronze_20260419_120000.jsonl
    └── ehr/
        └── date=2026-04-19/
            └── ehr_bronze_20260419_120000.jsonl
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from brainwatch.contracts.events import EEGChunkEvent, EHREvent  # noqa: F401

EEG_REQUIRED = {"patient_id", "session_id", "event_time", "site_id"}
EHR_REQUIRED = {"patient_id", "encounter_id", "event_time", "event_type"}


def _event_fingerprint(payload: dict[str, Any]) -> str:
    """Deterministic dedup key.

    Kim-Hung: implement.
      - ``key = "|".join([patient_id, session_id or encounter_id, event_time])``
      - ``hashlib.sha256(key.encode()).hexdigest()[:16]``
    """
    # Kim-Hung: code fingerprint here.
    pass


class BronzeWriter:
    """Append events to partitioned JSONL files in the bronze zone.

    Kim-Hung: implement the class. Stats dict shape:
        ``{"written": N, "duplicates": M, "errors": K}``.
    """

    def __init__(self, bronze_root: str | Path, dlq=None) -> None:
        # Kim-Hung: code init.
        #   - mkdir parents=True for ``bronze_root``
        #   - default DLQ = DeadLetterQueue(bronze_root / "_dead_letter")
        #   - init self._seen: set[str] for dedup
        #   - init self._stats counters
        pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def write_eeg(self, event: EEGChunkEvent) -> bool:
        """Validate + dedup + append. Return True if written, False if dropped.

        Kim-Hung: delegate to ``self._write("eeg", to_payload(event), EEG_REQUIRED,
        partition_key=event.site_id)``.
        """
        # Kim-Hung: code here.
        pass

    def write_ehr(self, event: EHREvent) -> bool:
        # Kim-Hung: same pattern as write_eeg, no partition_key.
        pass

    def write_raw(self, stream: str, payload: dict[str, Any]) -> bool:
        """Useful when records arrive as dicts (e.g. from Kafka). Pick the right
        ``required`` set based on ``stream`` ('eeg' or 'ehr')."""
        # Kim-Hung: code here.
        pass

    @property
    def stats(self) -> dict[str, int]:
        # Kim-Hung: return a copy of self._stats.
        pass

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _write(self, stream: str, payload: dict[str, Any],
               required: set[str], partition_key: str | None = None) -> bool:
        """The single code path used by all the public ``write_*`` methods.

        Kim-Hung: implement steps in order:
          1. validate_required_fields(payload, required) → if missing, route to
             DLQ with reason ``f"missing fields: {missing}"`` and bump errors.
          2. fingerprint = _event_fingerprint(payload). If already in
             ``self._seen``, bump duplicates and return False.
          3. compute partition path:
             ``bronze_root / stream / [site=<key>/] / date=YYYY-MM-DD``
          4. open ``{stream}_bronze_{ts}.jsonl`` in append mode and write a line.
          5. bump written, return True.
        """
        # Kim-Hung: code the validate → dedup → write pipeline here.
        pass

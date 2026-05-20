"""Bronze-zone writer with SHA256 deduplication and DLQ routing.

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

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from brainwatch.contracts.events import EEGChunkEvent, EHREvent
from brainwatch.ingestion.dead_letter import DeadLetterQueue

EEG_REQUIRED = {"patient_id", "session_id", "event_time", "site_id"}
EHR_REQUIRED = {"patient_id", "encounter_id", "event_time", "event_type"}


def _event_fingerprint(payload: dict[str, Any]) -> str:
    """Deterministic dedup key."""
    patient_id = payload.get("patient_id", "")
    session_id = payload.get("session_id") or payload.get("encounter_id", "")
    event_time = payload.get("event_time", "")
    key = "|".join([patient_id, str(session_id), event_time])
    return hashlib.sha256(key.encode()).hexdigest()[:16]


class BronzeWriter:
    """Append events to partitioned JSONL files in the bronze zone."""

    def __init__(self, bronze_root: str | Path, dlq=None) -> None:
        from brainwatch.ingestion.dead_letter import DeadLetterQueue
        self._bronze_root = Path(bronze_root)
        self._bronze_root.mkdir(parents=True, exist_ok=True)
        self._dlq = dlq or DeadLetterQueue(self._bronze_root / "_dead_letter")
        self._seen: set[str] = set()
        self._stats = {"written": 0, "duplicates": 0, "errors": 0}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def write_eeg(self, event: EEGChunkEvent) -> bool:
        """Validate + dedup + append. Return True if written, False if dropped."""
        return self._write("eeg", asdict(event), EEG_REQUIRED, partition_key=event.site_id)

    def write_ehr(self, event: EHREvent) -> bool:
        """Validate + dedup + append. Return True if written, False if dropped."""
        return self._write("ehr", asdict(event), EHR_REQUIRED)

    def write_raw(self, stream: str, payload: dict[str, Any]) -> bool:
        """Useful when records arrive as dicts (e.g. from Kafka). Pick the right
        ``required`` set based on ``stream`` ('eeg' or 'ehr')."""
        required = EEG_REQUIRED if stream == "eeg" else EHR_REQUIRED
        return self._write(stream, payload, required)

    @property
    def stats(self) -> dict[str, int]:
        return self._stats.copy()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _write(self, stream: str, payload: dict[str, Any],
               required: set[str], partition_key: str | None = None) -> bool:
        """The single code path used by all the public ``write_*`` methods."""
        # 1. Validate required fields
        missing = validate_required_fields(payload, required)
        if missing:
            self._dlq.route(payload, f"missing fields: {', '.join(sorted(missing))}")
            self._stats["errors"] += 1
            return False

        # 2. Dedup via fingerprint
        fingerprint = _event_fingerprint(payload)
        if fingerprint in self._seen:
            self._stats["duplicates"] += 1
            return False
        self._seen.add(fingerprint)

        now = datetime.now(timezone.utc)
        date_part = f"date={now.strftime('%Y-%m-%d')}"
        if partition_key:
            partition_dir = self._bronze_root / stream / f"site={partition_key}" / date_part
        else:
            partition_dir = self._bronze_root / stream / date_part
        partition_dir.mkdir(parents=True, exist_ok=True)

        # 4. Write JSONL line
        ts = now.strftime("%Y%m%d_%H%M%S")
        file_path = partition_dir / f"{stream}_bronze_{ts}.jsonl"
        # If file exists and has content, append; otherwise create new
        with file_path.open("a") as f:
            f.write(json.dumps(payload, default=str) + "\n")

        self._stats["written"] += 1
        return True


def validate_required_fields(payload: dict[str, Any], required_fields: set[str]) -> list[str]:
    """Return list of missing/empty required fields."""
    return [field for field in sorted(required_fields)
            if payload.get(field) in (None, "")]
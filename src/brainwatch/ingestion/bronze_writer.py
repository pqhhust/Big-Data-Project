"""Write validated events to the bronze zone with deduplication.

Bronze zone layout::

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
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from brainwatch.contracts.events import (
    EEGChunkEvent,
    EHREvent,
    to_payload,
    validate_required_fields,
)
from brainwatch.ingestion.dead_letter import DeadLetterQueue

logger = logging.getLogger(__name__)

EEG_REQUIRED = {"patient_id", "session_id", "event_time", "site_id"}
EHR_REQUIRED = {"patient_id", "encounter_id", "event_time", "event_type"}


def _event_fingerprint(payload: dict[str, Any]) -> str:
    """Deterministic hash for dedup: patient + session/encounter + event_time."""
    key_parts = [
        payload.get("patient_id", ""),
        payload.get("session_id", "") or payload.get("encounter_id", ""),
        payload.get("event_time", ""),
    ]
    return hashlib.sha256("|".join(key_parts).encode()).hexdigest()[:16]


class BronzeWriter:
    """Append events to partitioned JSONL files in the bronze zone."""

    def __init__(self, bronze_root: str | Path, dlq: DeadLetterQueue | None = None) -> None:
        self._root = Path(bronze_root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._dlq = dlq or DeadLetterQueue(self._root / "_dead_letter")
        self._seen: set[str] = set()
        self._stats = {"written": 0, "duplicates": 0, "errors": 0}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def write_eeg(self, event: EEGChunkEvent) -> bool:
        return self._write("eeg", to_payload(event), EEG_REQUIRED,
                           partition_key=event.site_id)

    def write_ehr(self, event: EHREvent) -> bool:
        return self._write("ehr", to_payload(event), EHR_REQUIRED)

    def write_raw(self, stream: str, payload: dict[str, Any]) -> bool:
        required = EEG_REQUIRED if stream == "eeg" else EHR_REQUIRED
        partition_key = payload.get("site_id") if stream == "eeg" else None
        return self._write(stream, payload, required, partition_key=partition_key)

    @property
    def stats(self) -> dict[str, int]:
        return dict(self._stats)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _write(
        self,
        stream: str,
        payload: dict[str, Any],
        required: set[str],
        partition_key: str | None = None,
    ) -> bool:
        # Validate
        missing = validate_required_fields(payload, required)
        if missing:
            self._dlq.route(payload, reason=f"missing fields: {missing}")
            self._stats["errors"] += 1
            return False

        # Dedup
        fp = _event_fingerprint(payload)
        if fp in self._seen:
            self._stats["duplicates"] += 1
            return False
        self._seen.add(fp)

        # Determine partition path
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        parts = [stream]
        if partition_key:
            parts.append(f"site={partition_key}")
        parts.append(f"date={today}")
        out_dir = self._root
        for p in parts:
            out_dir = out_dir / p
        out_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_path = out_dir / f"{stream}_bronze_{ts}.jsonl"
        with out_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, default=str) + "\n")

        self._stats["written"] += 1
        return True

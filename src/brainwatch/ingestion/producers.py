"""Kafka and file-based event producers for EEG and EHR data streams."""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from brainwatch.contracts.events import (
    EEGChunkEvent,
    EHREvent,
    compute_fingerprint,
    to_json,
)
from brainwatch.contracts.validators import validate_eeg_event, validate_ehr_event
from brainwatch.ingestion.dlq import DeadLetterQueue
from brainwatch.ingestion.writers import EventWriter, FileEventWriter

logger = logging.getLogger(__name__)


class EEGProducer:
    """Reads EEG session manifests and produces ``EEGChunkEvent`` records.

    Supports both Kafka and file-fallback modes.  When Kafka is unavailable
    the producer silently switches to JSONL output.
    """

    def __init__(
        self,
        writer: EventWriter,
        topic: str = "eeg.raw",
        dlq: DeadLetterQueue | None = None,
        chunk_duration_sec: float = 1.0,
    ) -> None:
        self.writer = writer
        self.topic = topic
        self.dlq = dlq
        self.chunk_duration_sec = chunk_duration_sec
        self._produced = 0
        self._failed = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def produce_from_manifest(self, manifest_path: str | Path) -> dict[str, int]:
        """Read a download manifest and produce EEG events for every record."""
        manifest_path = Path(manifest_path)
        with manifest_path.open("r", encoding="utf-8") as fh:
            manifest = json.load(fh)

        records = manifest.get("records", [])
        logger.info("Producing EEG events from %d manifest records", len(records))

        for record in records:
            for event in self._events_from_manifest_record(record):
                self._send_event(event)

        self.writer.flush()
        stats = {"produced": self._produced, "failed": self._failed}
        logger.info("EEG production complete: %s", stats)
        return stats

    def produce_events(self, events: list[EEGChunkEvent]) -> dict[str, int]:
        """Produce a list of pre-built events."""
        for event in events:
            self._send_event(event)
        self.writer.flush()
        return {"produced": self._produced, "failed": self._failed}

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _events_from_manifest_record(self, record: dict[str, Any]) -> Iterator[EEGChunkEvent]:
        """Generate chunk events from a single manifest entry."""
        duration = record.get("duration_seconds", 0)
        n_chunks = max(1, int(duration / self.chunk_duration_sec))

        base_time = datetime.now(timezone.utc)

        for i in range(n_chunks):
            offset_sec = i * self.chunk_duration_sec
            event_time = base_time.replace(
                second=int(offset_sec % 60),
                microsecond=int((offset_sec * 1_000_000) % 1_000_000),
            )
            yield EEGChunkEvent(
                patient_id=record.get("subject_id", f"P-{uuid.uuid4().hex[:8]}"),
                session_id=record.get("session_id", f"S-{uuid.uuid4().hex[:8]}"),
                event_time=event_time.isoformat(),
                site_id=record.get("site_id", "UNKNOWN"),
                channel_count=21,
                sampling_rate_hz=256.0,
                window_seconds=self.chunk_duration_sec,
                source_uri=record.get("candidate_source_keys", [""])[0] if record.get("candidate_source_keys") else "",
            )

    def _send_event(self, event: EEGChunkEvent) -> None:
        payload = event.to_dict()
        errors = validate_eeg_event(payload)
        if errors:
            logger.warning("EEG validation failed: %s", errors)
            if self.dlq:
                self.dlq.send(self.topic, payload, "; ".join(errors))
            self._failed += 1
            return

        try:
            self.writer.write(self.topic, event.patient_id, to_json(event))
            self._produced += 1
        except Exception:
            logger.exception("Failed to produce EEG event for %s", event.patient_id)
            if self.dlq:
                self.dlq.send(self.topic, payload, "Producer write failure")
            self._failed += 1


class EHRProducer:
    """Produces ``EHREvent`` records from EHR data sources."""

    def __init__(
        self,
        writer: EventWriter,
        topic: str = "ehr.updates",
        dlq: DeadLetterQueue | None = None,
    ) -> None:
        self.writer = writer
        self.topic = topic
        self.dlq = dlq
        self._produced = 0
        self._failed = 0

    def produce_events(self, events: list[EHREvent]) -> dict[str, int]:
        """Produce a list of EHR events."""
        for event in events:
            self._send_event(event)
        self.writer.flush()
        return {"produced": self._produced, "failed": self._failed}

    def produce_from_jsonl(self, jsonl_path: str | Path) -> dict[str, int]:
        """Read JSONL file and produce EHR events."""
        jsonl_path = Path(jsonl_path)
        with jsonl_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                event = EHREvent.from_dict(data)
                self._send_event(event)

        self.writer.flush()
        return {"produced": self._produced, "failed": self._failed}

    def _send_event(self, event: EHREvent) -> None:
        payload = event.to_dict()
        errors = validate_ehr_event(payload)
        if errors:
            logger.warning("EHR validation failed: %s", errors)
            if self.dlq:
                self.dlq.send(self.topic, payload, "; ".join(errors))
            self._failed += 1
            return

        try:
            self.writer.write(self.topic, event.patient_id, to_json(event))
            self._produced += 1
        except Exception:
            logger.exception("Failed to produce EHR event for %s", event.patient_id)
            if self.dlq:
                self.dlq.send(self.topic, payload, "Producer write failure")
            self._failed += 1

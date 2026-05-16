"""Synthetic EHR generator + normaliser.

Owner: **Kim-Quan**.
Depends on: ``brainwatch.contracts.events.EHREvent``, Kim-Hung's
``kafka_helpers.get_producer``.

Why synthetic? The BDSP corpus only ships EEG signals; the matching EHR side
is private hospital data. For Week 2 we generate plausible synthetic EHR events
for each subject in the download manifest, so the join in the speed layer
(Quang-Hung's bronze_ingest) has something realistic to land on.
"""
from __future__ import annotations

import json
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from brainwatch.contracts.events import EHREvent

EHR_EVENT_TYPES = ("vital_signs", "lab_result", "medication", "critical_lab", "note")


def _iso_z(value: datetime) -> str:
  return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_manifest_records(manifest_path: Path) -> list[dict[str, Any]]:
  with manifest_path.open("r", encoding="utf-8") as handle:
    payload = json.load(handle)

  if isinstance(payload, dict):
    records = payload.get("records")
    if isinstance(records, list):
      return [record for record in records if isinstance(record, dict)]
  if isinstance(payload, list):
    return [record for record in payload if isinstance(record, dict)]
  return []


def generate_ehr_from_manifest(manifest_path: Path,
                               events_per_subject: int = 5) -> list[EHREvent]:
    """For each subject in the manifest, emit ``events_per_subject`` synthetic
    ``EHREvent`` instances spread across the 5 event types.

    Suggested distribution (you can tweak — just keep ``critical_lab`` rare):
      vital_signs 40%, lab_result 25%, medication 20%, note 10%, critical_lab 5%.

    Field generation tips:
      - ``encounter_id`` = ``f"enc_{patient_id}_{i:03d}"``
      - ``event_time`` = ISO-8601, jitter around ``datetime.utcnow()`` by ±30 min
      - ``source_system`` = ``"epic"`` or ``"cerner"`` (random)
      - ``version`` = 1
      - ``payload`` = small dict with type-appropriate fields:
          vital_signs   → {"hr": 72, "spo2": 0.98, "rr": 16}
          lab_result    → {"test": "Na", "value": 138, "unit": "mEq/L"}
          medication    → {"drug": "levetiracetam", "dose_mg": 500}
          critical_lab  → {"test": "lactate", "value": 4.5, "unit": "mmol/L"}
          note          → {"text": "Routine neuro check, no AED change"}

    Kim-Quan: implement.
    """
    records = _load_manifest_records(manifest_path)
    if not records:
      return []

    weights = {
      "vital_signs": 0.40,
      "lab_result": 0.25,
      "medication": 0.20,
      "note": 0.10,
      "critical_lab": 0.05,
    }
    current_time = datetime.now(timezone.utc)
    events: list[EHREvent] = []

    for record_index, record in enumerate(records):
      patient_id = str(record.get("subject_id") or record.get("patient_id") or f"subject-{record_index:04d}")
      subject_seed = f"{patient_id}:{record.get('session_id', '0')}"
      rng = random.Random(subject_seed)

      for event_index in range(events_per_subject):
        event_type = rng.choices(list(weights.keys()), weights=list(weights.values()), k=1)[0]
        event_time = current_time + timedelta(minutes=rng.randint(-30, 30))
        encounter_id = f"enc_{patient_id}_{event_index:03d}"
        source_system = rng.choice(["epic", "cerner"])

        if event_type == "vital_signs":
          payload = {"hr": rng.randint(60, 110), "spo2": round(rng.uniform(0.92, 0.99), 2), "rr": rng.randint(12, 20)}
        elif event_type == "lab_result":
          payload = {"test": "Na", "value": rng.randint(134, 145), "unit": "mEq/L"}
        elif event_type == "medication":
          payload = {"drug": "levetiracetam", "dose_mg": 500}
        elif event_type == "critical_lab":
          payload = {"test": "lactate", "value": round(rng.uniform(3.5, 6.0), 1), "unit": "mmol/L"}
        else:
          payload = {"text": "Routine neuro check, no AED change"}

        events.append(
          EHREvent(
            patient_id=patient_id,
            encounter_id=encounter_id,
            event_time=_iso_z(event_time),
            event_type=event_type,
            source_system=source_system,
            version=1,
            payload=payload,
          )
        )

    return events


def normalize_ehr_payload(raw: dict[str, Any]) -> EHREvent:
    """Coerce a free-form dict into a valid ``EHREvent``.

    Steps:
      1. lower-case ``event_type``; default to ``"note"`` if missing.
      2. ensure ``event_time`` is an ISO-8601 string (parse + reformat if it's
         already a ``datetime``).
      3. default ``version=1`` if missing.
      4. ``source_system`` default = ``"unknown"``.
      5. unknown keys (anything not in the dataclass) go into ``payload``.

    Raises ``ValueError`` if ``patient_id`` or ``encounter_id`` is missing —
    those are the two we genuinely cannot synthesise.

    Kim-Quan: implement.
    """
    patient_id = raw.get("patient_id") or raw.get("PatientID")
    encounter_id = raw.get("encounter_id") or raw.get("EncounterID")
    if not patient_id or not encounter_id:
      raise ValueError("patient_id and encounter_id are required")

    raw_event_type = str(raw.get("event_type") or raw.get("EventType") or "note")
    event_type = raw_event_type.lower()
    source_system = str(raw.get("source_system") or raw.get("SourceSystem") or "unknown")
    version = int(raw.get("version") or raw.get("Version") or 1)

    raw_event_time = raw.get("event_time") or raw.get("EventTime")
    if isinstance(raw_event_time, datetime):
      event_time = _iso_z(raw_event_time)
    elif raw_event_time:
      event_time_text = str(raw_event_time).strip().replace(" ", "T")
      if event_time_text.endswith("Z"):
        event_time_text = event_time_text[:-1] + "+00:00"
      parsed_event_time = datetime.fromisoformat(event_time_text)
      if parsed_event_time.tzinfo is None:
        parsed_event_time = parsed_event_time.replace(tzinfo=timezone.utc)
      event_time = _iso_z(parsed_event_time)
    else:
      event_time = _iso_z(datetime.now(timezone.utc))

    payload = {
      key: value
      for key, value in raw.items()
      if key not in {
        "patient_id",
        "PatientID",
        "encounter_id",
        "EncounterID",
        "event_time",
        "EventTime",
        "event_type",
        "EventType",
        "source_system",
        "SourceSystem",
        "version",
        "Version",
      }
    }

    return EHREvent(
      patient_id=str(patient_id),
      encounter_id=str(encounter_id),
      event_time=event_time,
      event_type=event_type,
      source_system=source_system,
      version=version,
      payload=payload,
    )


def publish_ehr_events(
    events: list[EHREvent],
    topic: str = "ehr.updates",
    bootstrap_servers: str = "localhost:9092",
    replay_speed: float = 0.0,
    fallback_path: str | None = None,
) -> dict[str, Any]:
    """Mirror of ``eeg_producer.publish_events`` for the ``ehr.updates`` topic.

    Kim-Quan: same shape — ``get_producer``, validate, send, flush. Return
    ``{"published": N, "failed": M, "validation_errors": K}``.
    """
    from brainwatch.ingestion.kafka_helpers import get_producer

    producer = get_producer(bootstrap_servers=bootstrap_servers, fallback_path=fallback_path)
    published = 0
    failed = 0
    validation_errors = 0

    for index, event in enumerate(events):
      if not event.patient_id or not event.encounter_id:
        validation_errors += 1
        failed += 1
        continue

      try:
        producer.send(topic, event)
        published += 1
        if replay_speed > 0 and index < len(events) - 1:
          time.sleep(1.0 / replay_speed)
      except Exception:
        failed += 1

    producer.flush()
    close = getattr(producer, "close", None)
    if callable(close):
      close()

    return {"published": published, "failed": failed, "validation_errors": validation_errors}

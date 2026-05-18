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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from brainwatch.contracts.events import EHREvent
from brainwatch.ingestion.kafka_helpers import get_producer

EHR_EVENT_TYPES = ("vital_signs", "lab_result", "medication", "critical_lab", "note")

# Distribution weights for event types
EHR_WEIGHTS = {
    "vital_signs": 0.40,
    "lab_result": 0.25,
    "medication": 0.20,
    "note": 0.10,
    "critical_lab": 0.05
}


def _generate_payload(event_type: str) -> dict[str, Any]:
    """Generate type-appropriate payload for an EHR event."""
    if event_type == "vital_signs":
        return {
            "hr": random.randint(50, 120),
            "spo2": round(random.uniform(0.92, 1.0), 2),
            "rr": random.randint(10, 25),
            "bp_systolic": random.randint(100, 160),
            "bp_diastolic": random.randint(60, 100)
        }
    elif event_type == "lab_result":
        tests = ["Na", "K", "Cl", "BUN", "Cr", "Glucose"]
        return {
            "test": random.choice(tests),
            "value": round(random.uniform(50, 200), 1),
            "unit": "mEq/L"
        }
    elif event_type == "medication":
        drugs = ["levetiracetam", "lamotrigine", "valproate", "topiramate"]
        return {
            "drug": random.choice(drugs),
            "dose_mg": random.choice([250, 500, 750, 1000])
        }
    elif event_type == "critical_lab":
        return {
            "test": "lactate",
            "value": round(random.uniform(3.0, 8.0), 1),
            "unit": "mmol/L",
            "flag": "CRITICAL"
        }
    else:  # note
        notes = [
            "Routine neuro check, no AED change",
            "Patient alert and oriented x3",
            "No seizure activity observed",
            "Pupils equal and reactive"
        ]
        return {"text": random.choice(notes)}


def generate_ehr_from_manifest(manifest_path: Path,
                               events_per_subject: int = 5) -> list[EHREvent]:
    """For each subject in the manifest, emit ``events_per_subject`` synthetic
    ``EHREvent`` instances spread across the 5 event types."""
    with manifest_path.open() as f:
        manifest = json.load(f)

    events = []
    base_time = datetime.now(timezone.utc)

    for record in manifest.get("records", []):
        patient_id = record["subject_id"]
        site_id = record["site_id"]

        # Generate events distributed across types
        for i in range(events_per_subject):
            # Select event type based on weights
            event_type = random.choices(
                list(EHR_WEIGHTS.keys()),
                weights=list(EHR_WEIGHTS.values())
            )[0]

            # Jitter time around base_time by ±30 min
            jitter = timedelta(minutes=random.randint(-30, 30))
            event_time = base_time + jitter

            event = EHREvent(
                patient_id=patient_id,
                encounter_id=f"enc_{patient_id}_{site_id}_{i:03d}",
                event_time=event_time.isoformat(),
                event_type=event_type,
                source_system=random.choice(["epic", "cerner"]),
                version=1,
                payload=_generate_payload(event_type)
            )
            events.append(event)

    return events


def normalize_ehr_payload(raw: dict[str, Any]) -> EHREvent:
    """Coerce a free-form dict into a valid ``EHREvent``."""
    if not raw.get("patient_id"):
        raise ValueError("patient_id is required")
    if not raw.get("encounter_id"):
        raise ValueError("encounter_id is required")

    # Normalize event_type
    event_type = raw.get("event_type", "note").lower()
    if event_type not in EHR_EVENT_TYPES:
        event_type = "note"

    # Normalize event_time to ISO-8601
    event_time = raw.get("event_time", "")
    if isinstance(event_time, datetime):
        event_time = event_time.isoformat()

    return EHREvent(
        patient_id=raw["patient_id"],
        encounter_id=raw["encounter_id"],
        event_time=str(event_time),
        event_type=event_type,
        source_system=raw.get("source_system", "unknown"),
        version=raw.get("version", 1),
        payload={k: v for k, v in raw.items()
                 if k not in ("patient_id", "encounter_id", "event_time",
                              "event_type", "source_system", "version")}
    )


def publish_ehr_events(
    events: list[EHREvent],
    topic: str = "ehr.updates",
    bootstrap_servers: str = "localhost:9092",
    replay_speed: float = 0.0,
    fallback_path: str | None = None,
) -> dict[str, Any]:
    """Mirror of ``eeg_producer.publish_events`` for the ``ehr.updates`` topic."""
    from dataclasses import asdict
    producer = get_producer(bootstrap_servers, fallback_path)

    stats = {"published": 0, "failed": 0, "validation_errors": 0}

    for event in events:
        payload = asdict(event)
        missing = [f for f in EHR_REQUIRED if payload.get(f) in (None, "")]
        if missing:
            stats["validation_errors"] += 1
            continue

        try:
            producer.send(topic, value=payload)
            stats["published"] += 1

            if replay_speed > 0:
                time.sleep(1.0 / replay_speed)
        except Exception:
            stats["failed"] += 1

    producer.flush()
    producer.close()
    return stats


EHR_REQUIRED = {"patient_id", "encounter_id", "event_time", "event_type"}
"""Synthetic EHR event generator for BrainWatch.

Generates realistic EHR update events (labs, vitals, diagnoses, medications)
paired with patient metadata.  Supports both synthetic mode (random generation)
and real mode (from HEEDB ICD-10 data).
"""

from __future__ import annotations

import csv
import json
import random
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from brainwatch.contracts.events import EHREvent


# ---------------------------------------------------------------------------
# ICD-10 neurology diagnosis codes (subset for synthetic generation)
# ---------------------------------------------------------------------------

NEUROLOGY_ICD10_CODES: list[dict[str, str]] = [
    {"code": "G40.0", "description": "Localization-related epilepsy with seizures"},
    {"code": "G40.1", "description": "Localization-related epilepsy without seizures"},
    {"code": "G40.2", "description": "Generalized idiopathic epilepsy"},
    {"code": "G40.3", "description": "Generalized symptomatic epilepsy"},
    {"code": "G41.0", "description": "Status epilepticus, grand mal"},
    {"code": "G41.2", "description": "Complex partial status epilepticus"},
    {"code": "G43.0", "description": "Migraine without aura"},
    {"code": "G43.1", "description": "Migraine with aura"},
    {"code": "G47.0", "description": "Insomnia"},
    {"code": "G47.3", "description": "Sleep apnea"},
    {"code": "G93.1", "description": "Anoxic brain damage"},
    {"code": "G93.4", "description": "Encephalopathy, unspecified"},
    {"code": "R56.9", "description": "Unspecified convulsions"},
    {"code": "I63.9", "description": "Cerebral infarction, unspecified"},
    {"code": "S06.0", "description": "Concussion"},
]

EHR_EVENT_TYPES: list[str] = [
    "admission",
    "lab_result",
    "critical_lab",
    "vital_signs",
    "medication",
    "diagnosis",
    "discharge",
]

SOURCE_SYSTEMS: list[str] = ["EMR-A", "EMR-B", "LAB-CORE", "PHARMACY", "ADT"]


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

def generate_synthetic_ehr_events(
    patient_ids: list[str],
    events_per_patient: int = 5,
    base_time: datetime | None = None,
    seed: int | None = None,
) -> list[EHREvent]:
    """Generate synthetic EHR events for a list of patient IDs.

    Each patient receives ``events_per_patient`` events spanning a
    randomized time window.
    """
    if seed is not None:
        random.seed(seed)

    if base_time is None:
        base_time = datetime.now(timezone.utc)

    events: list[EHREvent] = []
    for patient_id in patient_ids:
        patient_events = _generate_patient_timeline(patient_id, events_per_patient, base_time)
        events.extend(patient_events)

    return events


def _generate_patient_timeline(
    patient_id: str,
    count: int,
    base_time: datetime,
) -> list[EHREvent]:
    """Generate a realistic sequence of EHR events for a single patient."""
    events: list[EHREvent] = []
    encounter_id = f"ENC-{uuid.uuid4().hex[:8]}"
    source = random.choice(SOURCE_SYSTEMS)
    diagnosis = random.choice(NEUROLOGY_ICD10_CODES)

    for i in range(count):
        offset_minutes = random.randint(0, 1440)  # spread over 24h
        event_time = base_time + timedelta(minutes=offset_minutes)
        event_type = EHR_EVENT_TYPES[i % len(EHR_EVENT_TYPES)]
        payload = _build_payload(event_type, diagnosis)

        events.append(
            EHREvent(
                patient_id=patient_id,
                encounter_id=encounter_id,
                event_time=event_time.isoformat(),
                event_type=event_type,
                source_system=source,
                version=1,
                payload=payload,
            )
        )

    return events


def _build_payload(event_type: str, diagnosis: dict[str, str]) -> dict[str, Any]:
    """Build a realistic payload dictionary based on the event type."""
    if event_type == "admission":
        return {
            "reason": diagnosis["description"],
            "icd10_code": diagnosis["code"],
            "priority": random.choice(["urgent", "emergent", "routine"]),
        }
    elif event_type == "lab_result":
        return {
            "test_name": random.choice(["CBC", "BMP", "CMP", "LFT", "Coag", "Ammonia", "Lactate"]),
            "value": str(round(random.uniform(0.5, 15.0), 2)),
            "unit": random.choice(["mg/dL", "mmol/L", "U/L", "g/dL"]),
            "reference_range": "0.5-10.0",
            "flag": random.choice(["normal", "high", "low", "critical"]),
        }
    elif event_type == "critical_lab":
        return {
            "test_name": random.choice(["Sodium", "Potassium", "Glucose", "Troponin"]),
            "value": str(round(random.uniform(120, 180), 1)),
            "unit": "mEq/L",
            "flag": "critical",
            "notification_sent": "true",
        }
    elif event_type == "vital_signs":
        return {
            "heart_rate": str(random.randint(50, 120)),
            "blood_pressure_systolic": str(random.randint(90, 180)),
            "blood_pressure_diastolic": str(random.randint(50, 110)),
            "temperature_c": str(round(random.uniform(36.0, 39.5), 1)),
            "respiratory_rate": str(random.randint(12, 28)),
            "spo2": str(random.randint(88, 100)),
        }
    elif event_type == "medication":
        return {
            "drug_name": random.choice([
                "Levetiracetam", "Lacosamide", "Phenytoin",
                "Valproic Acid", "Lorazepam", "Midazolam",
            ]),
            "dose": str(random.choice([250, 500, 750, 1000])),
            "unit": "mg",
            "route": random.choice(["IV", "PO", "IM"]),
        }
    elif event_type == "diagnosis":
        return {
            "icd10_code": diagnosis["code"],
            "description": diagnosis["description"],
            "type": random.choice(["primary", "secondary"]),
        }
    elif event_type == "discharge":
        return {
            "disposition": random.choice(["home", "rehab", "SNF", "AMA"]),
            "follow_up_days": str(random.choice([7, 14, 30])),
        }
    else:
        return {"note": "Unstructured clinical note"}


def write_ehr_events_jsonl(events: list[EHREvent], output_path: str | Path) -> None:
    """Write EHR events to a JSONL file."""
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(event.to_dict(), default=str) + "\n")


def load_real_ehr_from_csv(csv_path: str | Path) -> list[EHREvent]:
    """Load real EHR events from a CSV file with ICD-10 codes.

    Expected columns: patient_id, encounter_id, event_time, event_type,
    icd10_code, description.
    """
    events: list[EHREvent] = []
    with Path(csv_path).open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            events.append(
                EHREvent(
                    patient_id=row.get("patient_id", ""),
                    encounter_id=row.get("encounter_id", f"ENC-{uuid.uuid4().hex[:8]}"),
                    event_time=row.get("event_time", datetime.now(timezone.utc).isoformat()),
                    event_type=row.get("event_type", "diagnosis"),
                    source_system=row.get("source_system", "HEEDB"),
                    version=int(row.get("version", 1)),
                    payload={
                        "icd10_code": row.get("icd10_code", ""),
                        "description": row.get("description", ""),
                    },
                )
            )
    return events

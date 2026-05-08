"""EHR synthetic generation and raw-to-bronze normalisation.

Since BDSP provides EEG but not EHR records, this module generates
correlated synthetic EHR events for each EEG subject and normalises
them into the canonical ``EHREvent`` schema for the bronze zone.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from brainwatch.contracts.events import EHREvent, to_payload, validate_required_fields
from brainwatch.ingestion.kafka_helpers import get_producer

EHR_REQUIRED = {"patient_id", "encounter_id", "event_time", "event_type"}

# ---------------------------------------------------------------------------
# Synthetic EHR templates
# ---------------------------------------------------------------------------

DIAGNOSIS_CODES = [
    ("G40.0", "Epilepsy — localization-related idiopathic"),
    ("G40.1", "Epilepsy — localization-related symptomatic"),
    ("G40.3", "Generalized idiopathic epilepsy"),
    ("G41.0", "Status epilepticus — grand mal"),
    ("R56.9", "Unspecified convulsions"),
    ("G93.1", "Anoxic brain damage"),
    ("I63.9", "Cerebral infarction, unspecified"),
    ("G43.9", "Migraine, unspecified"),
]

LAB_TEMPLATES = [
    {"test": "sodium", "value_range": (130, 150), "unit": "mEq/L", "critical_low": 125, "critical_high": 155},
    {"test": "potassium", "value_range": (3.2, 5.5), "unit": "mEq/L", "critical_low": 2.5, "critical_high": 6.5},
    {"test": "glucose", "value_range": (60, 200), "unit": "mg/dL", "critical_low": 40, "critical_high": 400},
    {"test": "phenytoin_level", "value_range": (5, 25), "unit": "mcg/mL", "critical_low": 0, "critical_high": 30},
    {"test": "levetiracetam_level", "value_range": (10, 40), "unit": "mcg/mL", "critical_low": 0, "critical_high": 50},
    {"test": "creatinine", "value_range": (0.5, 1.5), "unit": "mg/dL", "critical_low": 0, "critical_high": 4.0},
]

EVENT_TYPES = ["admission", "diagnosis", "lab_result", "medication_order", "discharge", "critical_lab"]


def _deterministic_seed(subject_id: str) -> int:
    return int(hashlib.md5(subject_id.encode()).hexdigest()[:8], 16)


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

def generate_ehr_for_subject(
    subject_id: str,
    site_id: str,
    eeg_start: datetime | None = None,
    n_events: int = 5,
) -> list[EHREvent]:
    """Generate n_events synthetic EHR events for a single subject."""
    rng = random.Random(_deterministic_seed(subject_id))
    base_time = eeg_start or datetime.now(tz=timezone.utc) - timedelta(hours=rng.randint(1, 48))
    encounter_id = f"ENC-{site_id}-{subject_id[-6:]}"

    events: list[EHREvent] = []
    for i in range(n_events):
        offset = timedelta(minutes=rng.randint(0, 240), seconds=rng.randint(0, 59))
        event_time = base_time + offset
        event_type = rng.choice(EVENT_TYPES)

        if event_type == "diagnosis":
            code, desc = rng.choice(DIAGNOSIS_CODES)
            payload = {"diagnosis_code": code, "description": desc}
        elif event_type in ("lab_result", "critical_lab"):
            lab = rng.choice(LAB_TEMPLATES)
            value = round(rng.uniform(*lab["value_range"]), 2)
            is_critical = value < lab["critical_low"] or value > lab["critical_high"]
            if is_critical:
                event_type = "critical_lab"
            payload = {
                "test": lab["test"], "value": value,
                "unit": lab["unit"], "is_critical": is_critical,
            }
        elif event_type == "medication_order":
            payload = {
                "medication": rng.choice(["levetiracetam", "phenytoin", "valproate", "lacosamide"]),
                "dose_mg": rng.choice([250, 500, 750, 1000]),
                "route": "IV" if rng.random() < 0.3 else "PO",
            }
        else:
            payload = {"note": f"Synthetic {event_type} event"}

        events.append(EHREvent(
            patient_id=subject_id,
            encounter_id=encounter_id,
            event_time=event_time.isoformat(),
            event_type=event_type,
            source_system=f"BDSP-{site_id}",
            version=1,
            payload=payload,
        ))

    events.sort(key=lambda e: e.event_time)
    return events


def generate_ehr_from_manifest(
    manifest_path: Path,
    events_per_subject: int = 5,
) -> list[EHREvent]:
    """Generate synthetic EHR for every subject in a download manifest."""
    with manifest_path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)

    all_events: list[EHREvent] = []
    for rec in data.get("records", []):
        subject_id = rec.get("subject_id", "")
        site_id = rec.get("site_id", "")
        if not subject_id:
            continue
        all_events.extend(
            generate_ehr_for_subject(subject_id, site_id, n_events=events_per_subject)
        )
    return all_events


# ---------------------------------------------------------------------------
# Normalisation (raw dict → validated EHREvent)
# ---------------------------------------------------------------------------

def normalise_raw_ehr(raw: dict[str, Any]) -> EHREvent | None:
    """Normalise a raw EHR dict into the canonical EHREvent schema.

    Returns None if required fields are missing.
    """
    patient_id = raw.get("patient_id") or raw.get("PatientID") or raw.get("subject_id") or ""
    encounter_id = raw.get("encounter_id") or raw.get("EncounterID") or ""
    event_time = raw.get("event_time") or raw.get("EventTime") or raw.get("timestamp") or ""
    event_type = raw.get("event_type") or raw.get("EventType") or ""

    if not all([patient_id, encounter_id, event_time, event_type]):
        return None

    return EHREvent(
        patient_id=str(patient_id),
        encounter_id=str(encounter_id),
        event_time=str(event_time),
        event_type=str(event_type),
        source_system=str(raw.get("source_system", "unknown")),
        version=int(raw.get("version", 1)),
        payload={k: v for k, v in raw.items()
                 if k not in {"patient_id", "encounter_id", "event_time",
                              "event_type", "source_system", "version",
                              "PatientID", "EncounterID", "EventTime", "EventType"}},
    )


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------

def publish_ehr_events(
    events: list[EHREvent],
    topic: str = "ehr.updates",
    bootstrap_servers: str = "localhost:9092",
    fallback_path: str | None = None,
) -> dict[str, Any]:
    producer = get_producer(bootstrap_servers, fallback_path=fallback_path)
    sent, errors = 0, 0

    for ev in events:
        payload = to_payload(ev)
        missing = validate_required_fields(payload, EHR_REQUIRED)
        if missing:
            errors += 1
            continue
        producer.send(topic, value=ev)
        sent += 1

    producer.flush()
    producer.close()
    return {"sent": sent, "errors": errors, "total": len(events)}


# ---------------------------------------------------------------------------
# Bronze file writer
# ---------------------------------------------------------------------------

def write_ehr_bronze(events: list[EHREvent], output_dir: Path) -> Path:
    """Write EHR events as JSONL to the bronze zone."""
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = output_dir / f"ehr_bronze_{ts}.jsonl"
    with out_path.open("w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(to_payload(ev), default=str) + "\n")
    return out_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Generate synthetic EHR and publish.")
    p.add_argument("--manifest", required=True, type=Path, help="EEG download manifest")
    p.add_argument("--events-per-subject", type=int, default=5)
    p.add_argument("--topic", default="ehr.updates")
    p.add_argument("--bootstrap-servers", default="localhost:9092")
    p.add_argument("--fallback-path", default=None)
    p.add_argument("--bronze-dir", type=Path, default=Path("data/lake/bronze/ehr"),
                    help="Also write to bronze zone as JSONL")
    return p


def main() -> None:
    args = build_parser().parse_args()
    events = generate_ehr_from_manifest(args.manifest, args.events_per_subject)
    print(f"Generated {len(events)} synthetic EHR events")

    stats = publish_ehr_events(
        events, topic=args.topic,
        bootstrap_servers=args.bootstrap_servers,
        fallback_path=args.fallback_path,
    )
    print(f"Kafka publish: {json.dumps(stats)}")

    bronze_path = write_ehr_bronze(events, args.bronze_dir)
    print(f"Bronze zone: {bronze_path}")


if __name__ == "__main__":
    main()

"""EHR data loading and validation module.

Reads EHR source CSV files, validates schema, and generates EHREvent payloads.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from brainwatch.contracts.events import EHREvent


CANONICAL_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "patient_id": ("patient_id", "PatientID"),
    "encounter_id": ("encounter_id", "EncounterID"),
    "event_time": ("event_time", "EventTime"),
    "event_type": ("event_type", "EventType"),
    "source_system": ("source_system", "SourceSystem"),
    "version": ("version", "Version"),
}


def _first_present(row: dict[str, str], field_names: tuple[str, ...]) -> str | None:
    for field_name in field_names:
        raw_value = row.get(field_name)
        if raw_value is not None and raw_value.strip():
            return raw_value.strip()
    return None


def parse_ehr_timestamp(raw_value: str | None) -> str | None:
    """Parse a CSV timestamp into ISO-8601 with a UTC suffix."""
    if not raw_value or not raw_value.strip():
        return None

    normalized = raw_value.strip().replace(" ", "T")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def validate_ehr_row(row: dict[str, str]) -> tuple[bool, list[str]]:
    """Validate a single EHR record for required fields."""
    missing = []
    for canonical_name, aliases in CANONICAL_FIELD_ALIASES.items():
        if canonical_name == "version":
            continue
        if _first_present(row, aliases) is None:
            missing.append(aliases[-1])

    return len(missing) == 0, missing


def build_ehr_event(row: dict[str, str]) -> EHREvent | None:
    """Convert one CSV row to an ``EHREvent``."""
    is_valid, _missing_fields = validate_ehr_row(row)
    if not is_valid:
        return None

    patient_id = _first_present(row, CANONICAL_FIELD_ALIASES["patient_id"])
    encounter_id = _first_present(row, CANONICAL_FIELD_ALIASES["encounter_id"])
    event_time = parse_ehr_timestamp(
        _first_present(row, CANONICAL_FIELD_ALIASES["event_time"])
    )
    event_type = _first_present(row, CANONICAL_FIELD_ALIASES["event_type"])
    source_system = _first_present(row, CANONICAL_FIELD_ALIASES["source_system"])
    version_raw = _first_present(row, CANONICAL_FIELD_ALIASES["version"])

    if not all([patient_id, encounter_id, event_time, event_type, source_system]):
        return None

    canonical_fields = {alias for aliases in CANONICAL_FIELD_ALIASES.values() for alias in aliases}
    payload = {
        key: value
        for key, value in row.items()
        if key not in canonical_fields and value.strip()
    }

    try:
        version = int(version_raw) if version_raw is not None else 1
    except ValueError:
        version = 1

    return EHREvent(
        patient_id=patient_id,
        encounter_id=encounter_id,
        event_time=event_time,
        event_type=event_type,
        source_system=source_system,
        version=version,
        payload=payload,
    )


def summarize_ehr_metadata(csv_paths: Iterable[str | Path]) -> dict[str, Any]:
    """Generate summary statistics from EHR CSV files.
    
    Args:
        csv_paths: Iterable of paths to EHR CSV files
        
    Returns:
        Dictionary with summary metrics
    """
    patient_count: set[str] = set()
    encounter_count: Counter[str] = Counter()
    event_type_counts: Counter[str] = Counter()
    source_system_counts: Counter[str] = Counter()
    
    total_records = 0
    valid_records = 0
    invalid_records = 0
    invalid_reasons: Counter[str] = Counter()
    
    sample_events: list[dict[str, Any]] = []
    
    for csv_path in csv_paths:
        with Path(csv_path).open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                total_records += 1
                
                event = build_ehr_event(row)
                if event is None:
                    invalid_records += 1
                    _, missing = validate_ehr_row(row)
                    for field in missing:
                        invalid_reasons[f"missing_{field}"] += 1
                    continue
                
                valid_records += 1
                patient_count.add(event.patient_id)
                encounter_count[event.encounter_id] += 1
                event_type_counts[event.event_type] += 1
                source_system_counts[event.source_system] += 1
                
                if len(sample_events) < 5:
                    from brainwatch.contracts.events import to_payload

                    sample_events.append(to_payload(event))
    
    return {
        "total_records": total_records,
        "valid_records": valid_records,
        "invalid_records": invalid_records,
        "unique_patients": len(patient_count),
        "unique_encounters": len(encounter_count),
        "event_type_distribution": dict(event_type_counts),
        "source_system_distribution": dict(source_system_counts),
        "invalid_reasons": dict(invalid_reasons),
        "sample_events": sample_events,
    }


def main():
    """CLI entry point for EHR metadata summarization."""
    parser = argparse.ArgumentParser(
        description="Analyze and summarize EHR metadata from CSV files."
    )
    parser.add_argument(
        "--csv",
        type=str,
        nargs="+",
        required=True,
        help="Path(s) to EHR CSV file(s)",
    )
    parser.add_argument(
        "--output",
        type=str,
        required=True,
        help="Path to output summary JSON file",
    )
    
    args = parser.parse_args()
    
    summary = summarize_ehr_metadata(args.csv)
    
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    
    print(f"EHR summary written to {output_path}")
    print(f"  Total records: {summary['total_records']}")
    print(f"  Valid: {summary['valid_records']}")
    print(f"  Invalid: {summary['invalid_records']}")
    print(f"  Unique patients: {summary['unique_patients']}")


if __name__ == "__main__":
    main()

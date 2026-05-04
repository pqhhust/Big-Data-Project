"""EHR data loading and validation module.

Reads EHR source CSV files, validates schema, and generates EHREvent payloads.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from brainwatch.contracts.events import EHREvent


def parse_ehr_timestamp(raw_value: str | None) -> str | None:
    """Parse and validate EHR event timestamp.
    
    Args:
        raw_value: Raw timestamp string from CSV
        
    Returns:
        ISO-8601 formatted timestamp or None if invalid
    """
    if not raw_value or not raw_value.strip():
        return None
    # TODO: Implement timestamp parsing with timezone handling
    return raw_value.strip()


def validate_ehr_row(row: dict[str, str]) -> tuple[bool, list[str]]:
    """Validate a single EHR record for required fields.
    
    Args:
        row: Dictionary representing one EHR record
        
    Returns:
        (is_valid, list_of_missing_fields)
    """
    required_fields = {
        "PatientID",
        "EncounterID", 
        "EventTime",
        "EventType",
        "SourceSystem",
    }
    
    missing = []
    for field in required_fields:
        if field not in row or not row[field].strip():
            missing.append(field)
    
    return len(missing) == 0, missing


def build_ehr_event(row: dict[str, str]) -> EHREvent | None:
    """Convert CSV row to EHREvent dataclass.
    
    Args:
        row: Dictionary representing one EHR record
        
    Returns:
        EHREvent instance or None if invalid
    """
    is_valid, missing_fields = validate_ehr_row(row)
    if not is_valid:
        return None
    
    event_time = parse_ehr_timestamp(row.get("EventTime"))
    if not event_time:
        return None
    
    # Build payload from extra fields
    payload = {k: v for k, v in row.items() 
               if k not in {"PatientID", "EncounterID", "EventTime", "EventType", "SourceSystem", "Version"}}
    
    return EHREvent(
        patient_id=row["PatientID"].strip(),
        encounter_id=row["EncounterID"].strip(),
        event_time=event_time,
        event_type=row["EventType"].strip(),
        source_system=row["SourceSystem"].strip(),
        version=int(row.get("Version", 1)),
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

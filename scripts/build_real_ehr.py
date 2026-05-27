#!/usr/bin/env python3
"""Build real EHR data from HEEDB ICD-10 diagnoses.

Usage:
    python scripts/build_real_ehr.py --bronze data/lake/bronze_real
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build real EHR data with ICD-10 codes.")
    parser.add_argument("--bronze", required=True, help="Bronze output directory")
    parser.add_argument("--ehr-csv", default=None, help="Path to HEEDB ICD-10 CSV (optional)")
    parser.add_argument("--eeg-bronze", default=None, help="Path to EEG bronze JSONL for patient extraction")
    parser.add_argument("--events-per-patient", type=int, default=5, help="Events per patient")
    return parser


def extract_patient_ids_from_bronze(bronze_path: str | Path) -> list[str]:
    """Extract unique patient IDs from bronze EEG JSONL."""
    patient_ids: set[str] = set()
    bronze_dir = Path(bronze_path)

    for jsonl_file in bronze_dir.glob("*.jsonl"):
        with jsonl_file.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    pid = data.get("patient_id") or data.get("value", {}).get("patient_id")
                    if pid:
                        patient_ids.add(pid)
                except json.JSONDecodeError:
                    continue

    return sorted(patient_ids)


def main() -> None:
    args = build_parser().parse_args()

    bronze_dir = Path(args.bronze)
    bronze_dir.mkdir(parents=True, exist_ok=True)

    # Get patient IDs
    if args.eeg_bronze:
        patient_ids = extract_patient_ids_from_bronze(args.eeg_bronze)
    else:
        patient_ids = extract_patient_ids_from_bronze(args.bronze)

    if not patient_ids:
        # Generate synthetic patient IDs
        import uuid
        patient_ids = [f"P-{uuid.uuid4().hex[:8]}" for _ in range(100)]
        logger.info("No EEG bronze found — using %d synthetic patient IDs", len(patient_ids))

    logger.info("Found %d unique patients", len(patient_ids))

    # Generate EHR events
    if args.ehr_csv and Path(args.ehr_csv).exists():
        from brainwatch.ingestion.ehr_generator import load_real_ehr_from_csv, write_ehr_events_jsonl
        events = load_real_ehr_from_csv(args.ehr_csv)
        logger.info("Loaded %d real EHR events from %s", len(events), args.ehr_csv)
    else:
        from brainwatch.ingestion.ehr_generator import generate_synthetic_ehr_events, write_ehr_events_jsonl
        events = generate_synthetic_ehr_events(
            patient_ids=patient_ids,
            events_per_patient=args.events_per_patient,
            seed=42,
        )
        logger.info("Generated %d synthetic EHR events", len(events))

    output_path = bronze_dir / "ehr_bronze.jsonl"
    write_ehr_events_jsonl(events, output_path)
    logger.info("EHR bronze written: %d events → %s", len(events), output_path)

    summary = {
        "patient_count": len(patient_ids),
        "event_count": len(events),
        "output_path": str(output_path),
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

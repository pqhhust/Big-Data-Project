#!/usr/bin/env python3
"""Download EEG manifest and generate paired EHR events.

Usage:
    python scripts/download_eeg_ehr.py \
        --csv-dir ../STELAR-private/pretrain/reve/metadata \
        --output artifacts/week2/download_manifest.json \
        --target-hours 50
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def find_metadata_csvs(csv_dir: str | Path) -> list[Path]:
    """Find all metadata CSV files in the given directory."""
    csv_dir = Path(csv_dir)
    if not csv_dir.is_dir():
        logger.error("CSV directory not found: %s", csv_dir)
        return []
    csvs = sorted(csv_dir.glob("*_meta.csv"))
    if not csvs:
        csvs = sorted(csv_dir.glob("*.csv"))
    logger.info("Found %d metadata CSV files in %s", len(csvs), csv_dir)
    return csvs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate EEG download manifest and paired EHR events."
    )
    parser.add_argument(
        "--csv-dir",
        required=True,
        help="Directory containing site metadata CSVs",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output manifest JSON path",
    )
    parser.add_argument(
        "--target-hours",
        type=float,
        default=50.0,
        help="Target hours of EEG data (default: 50)",
    )
    parser.add_argument(
        "--max-sessions",
        type=int,
        default=200,
        help="Maximum sessions to select (default: 200)",
    )
    parser.add_argument(
        "--ehr-output",
        default=None,
        help="Optional: output path for generated EHR events JSONL",
    )
    parser.add_argument(
        "--events-per-patient",
        type=int,
        default=5,
        help="Synthetic EHR events per patient (default: 5)",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()

    # Find CSVs
    csv_paths = find_metadata_csvs(args.csv_dir)
    if not csv_paths:
        logger.error("No CSV files found in %s", args.csv_dir)
        sys.exit(1)

    # Build manifest
    from brainwatch.ingestion.subset_manifest import select_subset, write_manifest

    records = select_subset(
        csv_paths=[str(p) for p in csv_paths],
        max_sessions=args.max_sessions,
        target_hours=args.target_hours,
    )

    write_manifest(records, args.output)
    logger.info("Manifest written: %d records, %.1f hours → %s",
                len(records),
                sum(r["duration_seconds"] for r in records) / 3600.0,
                args.output)

    # Generate paired EHR events
    patient_ids = list({r["subject_id"] for r in records if r.get("subject_id")})

    if args.ehr_output or True:  # always generate
        from brainwatch.ingestion.ehr_generator import generate_synthetic_ehr_events, write_ehr_events_jsonl

        ehr_events = generate_synthetic_ehr_events(
            patient_ids=patient_ids,
            events_per_patient=args.events_per_patient,
            seed=42,
        )

        ehr_path = args.ehr_output or str(Path(args.output).parent / "ehr_events.jsonl")
        write_ehr_events_jsonl(ehr_events, ehr_path)
        logger.info("EHR events written: %d events for %d patients → %s",
                    len(ehr_events), len(patient_ids), ehr_path)

    # Summary
    summary = {
        "manifest_path": args.output,
        "record_count": len(records),
        "unique_patients": len(patient_ids),
        "total_hours": round(sum(r["duration_seconds"] for r in records) / 3600.0, 2),
        "ehr_events_generated": len(patient_ids) * args.events_per_patient,
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

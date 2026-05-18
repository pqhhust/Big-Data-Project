#!/usr/bin/env python3
"""Week-2 main entry: download a BDSP EEG subset and emit synthetic EHR.

Owner: **Trang** (CLI + manifest building, tests).
Pair with: **Quang-Hung** for the boto3 / S3 part the first time, **Kim-Quan**
for the EHR side, **Dat** for DLQ wiring on download failures.

Usage
-----
    # 1) Dry run — only build the manifest, no download:
    python scripts/download_eeg_ehr.py \\
        --csv-dir ../STELAR-private/pretrain/reve/metadata \\
        --output  artifacts/week2/download_manifest.json \\
        --target-hours 100 --dry-run

    # 2) Real download (requires AWS credentials in rootkey.csv):
    python scripts/download_eeg_ehr.py \\
        --csv-dir ../STELAR-private/pretrain/reve/metadata \\
        --output  artifacts/week2/download_manifest.json \\
        --download --download-root data/raw/eeg

    # 3) Override the credentials path:
    BDSP_CREDENTIALS=/path/to/rootkey.csv python scripts/download_eeg_ehr.py ...
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from brainwatch.ingestion.dead_letter import DeadLetterQueue
from brainwatch.ingestion.ehr_normalizer import generate_ehr_from_manifest

DEFAULT_CREDS = os.path.expanduser("~/credentials/rootkey.csv")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Credentials loader  ── Quang-Hung (lead) writes this once, everyone reuses.
# ---------------------------------------------------------------------------

def load_aws_credentials(path: str | Path) -> dict[str, str]:
    """Parse a 2-line AWS root-key CSV (`Access key ID,Secret access key`).

    Returns ``{"aws_access_key_id": ..., "aws_secret_access_key": ...}`` so it
    can be unpacked straight into ``boto3.client("s3", **creds)``.

    Never log or print the secret value.
    """
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        row = next(reader)
        return {
            "aws_access_key_id": row["Access key ID"],
            "aws_secret_access_key": row["Secret access key"]
        }


# ---------------------------------------------------------------------------
# Manifest builder  ── Trang
# ---------------------------------------------------------------------------

def build_manifest(
    csv_dir: Path,
    target_hours: float,
    min_duration: float,
    max_duration: float,
) -> dict[str, Any]:
    """Read every ``*_meta.csv`` in ``csv_dir`` and pick subjects until we hit
    ``target_hours`` of total recording time.

    Filter rules:
      - keep rows whose ``duration_seconds`` is between ``min_duration`` and
        ``max_duration``
      - prefer shorter recordings first (we want breadth, not whales)
      - stop once cumulative duration >= target_hours * 3600

    Returns a dict shaped like::

        {
          "target_hours": 100,
          "actual_hours": 99.7,
          "site_count": 5,
          "subject_count": 1436,
          "records": [
            {"subject_id": "...", "session_id": "...", "site_id": "S0001",
             "duration_seconds": 612.3, "s3_keys": ["bdsp-...edf"]},
            ...
          ]
        }
    """
    all_records = []

    # Read all *_meta.csv files
    for csv_file in Path(csv_dir).glob("*_meta.csv"):
        with csv_file.open(newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Handle different column name conventions
                duration_str = row.get("DurationInSeconds") or row.get("duration_seconds") or "0"
                try:
                    duration = float(duration_str)
                except ValueError:
                    continue
                # Filter by duration bounds
                if min_duration <= duration <= max_duration:
                    subject_id = row.get("BDSPPatientID") or row.get("subject_id") or "UNKNOWN"
                    session_id = row.get("SessionID") or row.get("session_id") or "1"
                    site_id = row.get("SiteID") or row.get("site_id") or "UNKNOWN"
                    s3_key = row.get("s3_key") or row.get("BidsFolder") or f"{site_id}/{subject_id}"
                    all_records.append({
                        "subject_id": subject_id,
                        "session_id": session_id,
                        "site_id": site_id,
                        "duration_seconds": duration,
                        "s3_keys": [s3_key]
                    })

    # Sort by duration (shorter first for breadth)
    all_records.sort(key=lambda r: r["duration_seconds"])

    # Accumulate until we hit target
    target_seconds = target_hours * 3600
    cumulative = 0.0
    selected = []
    sites = set()

    for record in all_records:
        cumulative += record["duration_seconds"]
        selected.append(record)
        sites.add(record["site_id"])
        if cumulative >= target_seconds:
            break

    actual_hours = cumulative / 3600

    return {
        "target_hours": target_hours,
        "actual_hours": round(actual_hours, 1),
        "site_count": len(sites),
        "subject_count": len(selected),
        "records": selected
    }


# ---------------------------------------------------------------------------
# S3 download  ── Kim-Hung (S3 / boto3 expertise; pair with Quang-Hung).
# ---------------------------------------------------------------------------

def download_subset(
    manifest: dict[str, Any],
    download_root: Path,
    credentials: dict[str, str],
    bucket: str = "bdsp-psg",
    dry_run: bool = False,
) -> dict[str, int]:
    """Download every ``s3_key`` listed in the manifest into ``download_root``.

    Layout on disk::

        download_root/site=<site_id>/<subject_id>/<filename>.edf

    Returns ``{"downloaded": N, "skipped": M, "failed": K}``.
    """
    try:
        import boto3
    except ImportError:
        logger.error("boto3 not installed; cannot download from S3")
        return {"downloaded": 0, "skipped": 0, "failed": len(manifest.get("records", []))}

    s3 = boto3.client("s3", **credentials)
    dlq = DeadLetterQueue(download_root / "_dlq")

    stats = {"downloaded": 0, "skipped": 0, "failed": 0}

    for record in manifest.get("records", []):
        site_id = record["site_id"]
        subject_id = record["subject_id"]

        for s3_key in record.get("s3_keys", []):
            local_path = download_root / f"site={site_id}" / subject_id / s3_key.split("/")[-1]

            if dry_run:
                print(f"Dry run: would download s3://{bucket}/{s3_key} -> {local_path}")
                stats["downloaded"] += 1
                continue

            # Skip if already exists
            if local_path.exists():
                stats["skipped"] += 1
                continue

            try:
                local_path.parent.mkdir(parents=True, exist_ok=True)
                s3.download_file(bucket, s3_key, str(local_path))
                stats["downloaded"] += 1
            except Exception as e:
                stats["failed"] += 1
                dlq.route({"s3_key": s3_key, "subject_id": subject_id}, f"download failed: {e}")

    return stats


# ---------------------------------------------------------------------------
# Synthetic EHR  ── Kim-Quan
# ---------------------------------------------------------------------------

def emit_synthetic_ehr(manifest: dict[str, Any], output_path: Path,
                       events_per_subject: int = 5) -> int:
    """For every subject in the manifest, emit ``events_per_subject`` synthetic
    EHR events to ``output_path`` (JSONL).

    Each event conforms to ``brainwatch.contracts.events.EHREvent``.
    """
    # Generate EHR events directly from manifest data (no temp file needed)
    from brainwatch.ingestion.ehr_normalizer import _generate_payload
    import random

    output_path.parent.mkdir(parents=True, exist_ok=True)
    from dataclasses import asdict
    from datetime import datetime, timezone, timedelta

    events = []
    base_time = datetime.now(timezone.utc)

    for record in manifest.get("records", []):
        patient_id = record["subject_id"]
        site_id = record["site_id"]

        for i in range(events_per_subject):
            event_type = random.choices(
                ["vital_signs", "lab_result", "medication", "note", "critical_lab"],
                weights=[0.40, 0.25, 0.20, 0.10, 0.05]
            )[0]

            jitter = timedelta(minutes=random.randint(-30, 30))
            event_time = base_time + jitter

            event = {
                "patient_id": patient_id,
                "encounter_id": f"enc_{patient_id}_{site_id}_{i:03d}",
                "event_time": event_time.isoformat(),
                "event_type": event_type,
                "source_system": random.choice(["epic", "cerner"]),
                "version": 1,
                "payload": _generate_payload(event_type)
            }
            events.append(event)

    # Write to JSONL
    with output_path.open("w") as f:
        for event in events:
            f.write(json.dumps(event, default=str) + "\n")

    return len(events)


# ---------------------------------------------------------------------------
# Main orchestration  ── Quang-Hung (lead)
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--csv-dir", type=Path, required=True,
                   help="Directory of BDSP metadata CSVs")
    p.add_argument("--output", type=Path, required=True,
                   help="Where to write the download manifest JSON")
    p.add_argument("--credentials", type=Path,
                   default=Path(os.getenv("BDSP_CREDENTIALS", DEFAULT_CREDS)),
                   help="Path to rootkey.csv (default: lab credentials)")
    p.add_argument("--target-hours", type=float, default=100.0)
    p.add_argument("--min-duration", type=float, default=60.0)
    p.add_argument("--max-duration", type=float, default=1200.0)
    p.add_argument("--download", action="store_true",
                   help="Actually download EDFs from S3")
    p.add_argument("--download-root", type=Path, default=Path("data/raw/eeg"))
    p.add_argument("--dry-run", action="store_true",
                   help="Print what would happen, write nothing")
    p.add_argument("--ehr-output", type=Path,
                   default=Path("artifacts/week2/synthetic_ehr.jsonl"))
    p.add_argument("--ehr-events-per-subject", type=int, default=5)
    return p


def main() -> None:
    args = build_parser().parse_args()

    # 1. Build manifest
    print(f"Building manifest from {args.csv_dir}...")
    manifest = build_manifest(
        args.csv_dir,
        target_hours=args.target_hours,
        min_duration=args.min_duration,
        max_duration=args.max_duration
    )
    print(f"Manifest: {manifest['subject_count']} subjects, {manifest['actual_hours']}h "
          f"across {manifest['site_count']} sites")

    # 2. Write manifest
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w") as f:
        json.dump(manifest, f, indent=2)
    print(f"Manifest written to {args.output}")

    # 3. Emit synthetic EHR
    print(f"Generating synthetic EHR...")
    ehr_count = emit_synthetic_ehr(
        manifest,
        args.ehr_output,
        events_per_subject=args.ehr_events_per_subject
    )
    print(f"Generated {ehr_count} EHR events -> {args.ehr_output}")

    # 4. Download if requested (not in dry-run mode)
    if args.download and not args.dry_run:
        print("Downloading EEG data from S3...")
        creds = load_aws_credentials(args.credentials)
        stats = download_subset(
            manifest,
            args.download_root,
            creds,
            dry_run=False
        )
        print(f"Download stats: {stats['downloaded']} downloaded, "
              f"{stats['skipped']} skipped, {stats['failed']} failed")

    # Summary
    print("\n=== Summary ===")
    print(f"Subjects: {manifest['subject_count']}")
    print(f"Hours: {manifest['actual_hours']}")
    print(f"Sites: {manifest['site_count']}")
    print(f"EHR events: {ehr_count}")


if __name__ == "__main__":
    main()
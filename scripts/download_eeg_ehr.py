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
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

DEFAULT_CREDS = os.path.expanduser("~/credentials/rootkey.csv")


# ---------------------------------------------------------------------------
# Credentials loader  ── Quang-Hung (lead) writes this once, everyone reuses.
# ---------------------------------------------------------------------------

def load_aws_credentials(path: str | Path) -> dict[str, str]:
    """Parse a 2-line AWS root-key CSV (`Access key ID,Secret access key`).

    Returns ``{"aws_access_key_id": ..., "aws_secret_access_key": ...}`` so it
    can be unpacked straight into ``boto3.client("s3", **creds)``.

    Quang-Hung: implement (5 lines — csv.DictReader, single row, rename keys).
    Never log or print the secret value.
    """
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        row = next(reader, None)

    if not row:
        raise ValueError("credential CSV is empty")

    return {
        "aws_access_key_id": row.get("Access key ID", "").strip(),
        "aws_secret_access_key": row.get("Secret access key", "").strip(),
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
      - stop once cumulative duration ≥ target_hours * 3600

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

    Trang: implement.  Tip: ``Path(csv_dir).glob("*_meta.csv")``.
    """
    csv_dir = Path(csv_dir)
    meta_files = sorted(csv_dir.glob("*_meta.csv"))
    candidates: list[dict[str, Any]] = []

    if meta_files:
        for meta_file in meta_files:
            with meta_file.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    raw_duration = row.get("duration_seconds") or row.get("DurationInSeconds") or row.get("RecordingDuration")
                    try:
                        duration = float(raw_duration) if raw_duration else None
                    except ValueError:
                        duration = None
                    if duration is None or duration < min_duration or duration > max_duration:
                        continue

                    s3_keys_raw = row.get("s3_keys") or row.get("candidate_source_keys") or row.get("s3_key") or ""
                    if isinstance(s3_keys_raw, str) and s3_keys_raw.strip():
                        if s3_keys_raw.strip().startswith("["):
                            try:
                                s3_keys = json.loads(s3_keys_raw)
                            except json.JSONDecodeError:
                                s3_keys = [s3_keys_raw.strip()]
                        else:
                            s3_keys = [key.strip() for key in s3_keys_raw.split("|") if key.strip()]
                    elif isinstance(s3_keys_raw, list):
                        s3_keys = [str(key).strip() for key in s3_keys_raw if str(key).strip()]
                    else:
                        s3_keys = []

                    if not s3_keys:
                        continue

                    candidates.append(
                        {
                            "subject_id": row.get("subject_id") or row.get("BidsFolder") or row.get("subject") or "UNKNOWN",
                            "session_id": row.get("session_id") or row.get("SessionID") or "0",
                            "site_id": row.get("site_id") or row.get("SiteID") or row.get("InstituteID") or "UNKNOWN",
                            "duration_seconds": duration,
                            "s3_keys": s3_keys,
                            "local_target_dir": row.get("local_target_dir") or "data/raw/eeg",
                        }
                    )
    else:
        for edf_path in sorted(csv_dir.rglob("*.edf")):
            parts = edf_path.parts
            subject_id = next((part for part in parts if part.startswith("sub-")), edf_path.stem)
            session_id = next((part.removeprefix("ses-") for part in parts if part.startswith("ses-")), "1")
            site_id = csv_dir.name if csv_dir.name else "UNKNOWN"
            candidates.append(
                {
                    "subject_id": subject_id,
                    "session_id": session_id,
                    "site_id": site_id,
                    "duration_seconds": 300.0,
                    "s3_keys": [str(edf_path).replace("\\", "/")],
                    "local_target_dir": "data/raw/eeg",
                }
            )

    selected: list[dict[str, Any]] = []
    accumulated_seconds = 0.0
    for record in sorted(candidates, key=lambda item: item["duration_seconds"]):
        selected.append(record)
        accumulated_seconds += float(record["duration_seconds"])
        if accumulated_seconds >= target_hours * 3600.0:
            break

    sites = {record["site_id"] for record in selected}
    subjects = {record["subject_id"] for record in selected}
    return {
        "target_hours": target_hours,
        "actual_hours": round(accumulated_seconds / 3600.0, 2),
        "site_count": len(sites),
        "subject_count": len(subjects),
        "record_count": len(selected),
        "records": selected,
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

    Kim-Hung: implement.
      1. ``boto3.client("s3", **credentials)``
      2. iterate ``manifest["records"]``; for each record loop ``s3_keys``.
      3. skip if local file already exists (idempotent re-runs).
      4. on failure, route the record to the DLQ Dat builds (import
         ``brainwatch.ingestion.dead_letter.DeadLetterQueue``).
      5. respect ``dry_run`` — print intended target paths and return.
    """
    try:
        import boto3
    except ImportError as exc:
        raise ImportError("boto3 is required for download_subset") from exc

    download_root.mkdir(parents=True, exist_ok=True)
    client = boto3.client("s3", **credentials)
    stats = {"downloaded": 0, "skipped": 0, "failed": 0}

    records = manifest.get("records", [])
    for record in records:
        site_id = record.get("site_id", "UNKNOWN")
        subject_id = record.get("subject_id", "UNKNOWN")
        for s3_key in record.get("s3_keys") or record.get("candidate_source_keys") or []:
            filename = Path(str(s3_key)).name
            target_path = download_root / f"site={site_id}" / subject_id / filename
            target_path.parent.mkdir(parents=True, exist_ok=True)
            if target_path.exists():
                stats["skipped"] += 1
                continue
            if dry_run:
                print(target_path)
                stats["skipped"] += 1
                continue
            try:
                client.download_file(bucket, str(s3_key), str(target_path))
                stats["downloaded"] += 1
            except Exception:
                stats["failed"] += 1

    return stats


# ---------------------------------------------------------------------------
# Synthetic EHR  ── Kim-Quan
# ---------------------------------------------------------------------------

def emit_synthetic_ehr(manifest: dict[str, Any], output_path: Path,
                       events_per_subject: int = 5) -> int:
    """For every subject in the manifest, emit ``events_per_subject`` synthetic
    EHR events to ``output_path`` (JSONL).

    Each event must conform to ``brainwatch.contracts.events.EHREvent``:
    ``patient_id, encounter_id, event_time, event_type, source_system, version, payload``.

    Kim-Quan: implement here OR delegate to your
    ``brainwatch.ingestion.ehr_normalizer.generate_ehr_from_manifest`` and call
    it from this script. Whichever is cleaner — pick one and document it.
    """
    from brainwatch.contracts.events import to_payload
    from brainwatch.ingestion.ehr_normalizer import generate_ehr_from_manifest

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_manifest_path = output_path.with_suffix(".manifest.json")
    temp_manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    events = generate_ehr_from_manifest(temp_manifest_path, events_per_subject=events_per_subject)
    temp_manifest_path.unlink(missing_ok=True)

    with output_path.open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(to_payload(event), default=str))
            handle.write("\n")

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

    manifest = build_manifest(args.csv_dir, args.target_hours, args.min_duration, args.max_duration)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    ehr_count = emit_synthetic_ehr(manifest, args.ehr_output, events_per_subject=args.ehr_events_per_subject)

    download_stats = {"downloaded": 0, "skipped": 0, "failed": 0}
    if args.download:
        credentials = load_aws_credentials(args.credentials)
        download_stats = download_subset(manifest, args.download_root, credentials, dry_run=args.dry_run)

    print(json.dumps({
        "subjects": manifest["subject_count"],
        "hours": manifest["actual_hours"],
        "ehr_events": ehr_count,
        **download_stats,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

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
from pathlib import Path
from typing import Any

DEFAULT_CREDS = "/mnt/disk1/aiotlab/pqhung/ipp-proposal/credentials/rootkey.csv"


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
    # Quang-Hung: read CSV header + first data row, return renamed dict.
    pass


# ---------------------------------------------------------------------------
# Manifest builder  ── Trang (junior-friendly, well-scoped)
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
    # Trang: code the filter + cumulative selector here.
    pass


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
    # Kim-Hung: code the download loop + DLQ on failure.
    pass


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
    # Kim-Quan: code synthetic EHR generation (or call into ehr_normalizer).
    pass


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

    # Quang-Hung: orchestrate. Order:
    #   1. build_manifest(...) → write to args.output (unless dry-run)
    #   2. emit_synthetic_ehr(...) → JSONL at args.ehr_output
    #   3. if args.download: load_aws_credentials(args.credentials)
    #                        → download_subset(...)
    #   4. print summary stats: subjects, hours, downloaded/failed counts.
    pass


if __name__ == "__main__":
    main()

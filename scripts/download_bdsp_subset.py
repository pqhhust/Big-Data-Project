#!/usr/bin/env python3
"""Download ~100 hours of short EEG recordings from 5 BDSP sites.

Strategy
--------
1. Read metadata CSVs for all 5 sites (S0001, S0002, I0002, I0003, I0009).
2. Filter recordings between ``--min-duration`` and ``--max-duration`` seconds.
   Default: 60–1200 s (1–20 min) to maximise unique-subject count.
3. Keep only **one session per subject** (shortest qualifying session).
4. Stratify across sites proportionally to each site's candidate pool.
5. Accumulate until ``--target-hours`` is reached.
6. Write a download manifest to ``--output``.
7. If ``--download`` is given, pull EDFs from BDSP S3 into ``--download-root``.

Usage
-----
  # Manifest only (no AWS credentials required):
  python scripts/download_bdsp_subset.py \\
      --output artifacts/week2/download_manifest.json

  # Manifest + download:
  python scripts/download_bdsp_subset.py \\
      --output artifacts/week2/download_manifest.json \\
      --download --download-root data/raw/eeg
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SITE_CSV_MAP: dict[str, str] = {
    "S0001": "S0001_meta.csv",
    "S0002": "S0002_meta.csv",
    "I0002": "I0002_meta.csv",
    "I0003": "I0003_meta.csv",
    "I0009": "I0009_meta.csv",
}

S3_ACCESS_POINT = (
    "arn:aws:s3:us-east-1:184438910517:"
    "accesspoint/bdsp-credentialed-access-point"
)

SERVICE_TASK_MAP: dict[str, dict[str, list[str]]] = {
    "S0001": {"LTM": ["cEEG"], "EMU": ["cEEG"], "Routine": ["rEEG"], "OR": ["OR"]},
    "S0002": {
        "LTM": ["cEEG", "EEG"], "Routine": ["rEEG"],
        "EMU": ["EMU"], "Faulkner": ["EEG"], "Fish": ["rEEG"],
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_task(site: str, service_name: str) -> list[str]:
    return SERVICE_TASK_MAP.get(site, {}).get(service_name, ["EEG"])


def _parse_duration(row: dict[str, str]) -> float | None:
    raw = row.get("DurationInSeconds") or row.get("RecordingDuration") or ""
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _build_s3_keys(row: dict[str, str]) -> list[str]:
    site = row.get("SiteID") or row.get("InstituteID") or ""
    bids = row.get("BidsFolder") or ""
    session_id = row.get("SessionID") or ""
    if not (site and bids and session_id):
        return []
    service = row.get("ServiceName", "")
    return [
        f"EEG/bids/{site}/{bids}/ses-{session_id}/eeg/"
        f"{bids}_ses-{session_id}_task-{task}_eeg.edf"
        for task in _resolve_task(site, service)
    ]


def _sex(row: dict[str, str]) -> str:
    raw = (row.get("SexDSC") or "").strip()
    if raw in ("Female", "F"):
        return "F"
    if raw in ("Male", "M"):
        return "M"
    return "U"


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def collect_candidates(
    csv_dir: Path,
    min_duration: float,
    max_duration: float,
) -> dict[str, list[dict[str, Any]]]:
    """Return {site: [candidate_records]} filtered by duration bounds."""
    candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for site, fname in SITE_CSV_MAP.items():
        csv_path = csv_dir / fname
        if not csv_path.exists():
            print(f"  [WARN] {csv_path} not found — skipping site {site}")
            continue

        seen_subjects: set[str] = set()
        site_candidates: list[dict[str, Any]] = []

        with csv_path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                dur = _parse_duration(row)
                if dur is None or dur < min_duration or dur > max_duration:
                    continue
                bids = row.get("BidsFolder") or ""
                if not bids or bids in seen_subjects:
                    continue
                keys = _build_s3_keys(row)
                if not keys:
                    continue
                seen_subjects.add(bids)
                site_candidates.append({
                    "site_id": site,
                    "subject_id": bids,
                    "session_id": row.get("SessionID") or "",
                    "duration_seconds": dur,
                    "service_name": row.get("ServiceName") or "UNSPECIFIED",
                    "sex": _sex(row),
                    "age": row.get("AgeAtVisit") or "",
                    "s3_keys": keys,
                })

        site_candidates.sort(key=lambda r: r["duration_seconds"])
        candidates[site] = site_candidates
        print(f"  {site}: {len(site_candidates)} unique-subject candidates "
              f"({min_duration}–{max_duration}s)")

    return dict(candidates)


def select_stratified(
    candidates: dict[str, list[dict[str, Any]]],
    target_hours: float,
) -> list[dict[str, Any]]:
    """Round-robin across sites until target_hours is reached."""
    selected: list[dict[str, Any]] = []
    accumulated_hours = 0.0
    site_idx: dict[str, int] = {s: 0 for s in candidates}
    exhausted: set[str] = set()

    while accumulated_hours < target_hours and len(exhausted) < len(candidates):
        for site in sorted(candidates.keys()):
            if site in exhausted:
                continue
            idx = site_idx[site]
            pool = candidates[site]
            if idx >= len(pool):
                exhausted.add(site)
                continue
            rec = pool[idx]
            selected.append(rec)
            accumulated_hours += rec["duration_seconds"] / 3600.0
            site_idx[site] = idx + 1
            if accumulated_hours >= target_hours:
                break

    return selected


def write_manifest(
    records: list[dict[str, Any]],
    output_path: Path,
) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    site_counts: dict[str, int] = defaultdict(int)
    site_hours: dict[str, float] = defaultdict(float)
    for r in records:
        site_counts[r["site_id"]] += 1
        site_hours[r["site_id"]] += r["duration_seconds"] / 3600.0

    summary = {
        "total_subjects": len(records),
        "total_hours": round(sum(r["duration_seconds"] for r in records) / 3600.0, 2),
        "total_s3_files": sum(len(r["s3_keys"]) for r in records),
        "subjects_by_site": dict(sorted(site_counts.items())),
        "hours_by_site": {k: round(v, 2) for k, v in sorted(site_hours.items())},
        "duration_range_seconds": {
            "min": min(r["duration_seconds"] for r in records) if records else 0,
            "max": max(r["duration_seconds"] for r in records) if records else 0,
        },
    }

    payload = {"manifest_version": "2.0", "summary": summary, "records": records}
    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=False)
        fh.write("\n")
    return summary


def download_from_s3(
    records: list[dict[str, Any]],
    download_root: Path,
    dry_run: bool = False,
) -> None:
    import boto3

    if dry_run:
        total = sum(len(r["s3_keys"]) for r in records)
        print(f"  [DRY-RUN] Would download {total} files to {download_root}")
        return

    s3 = boto3.client("s3")
    total = sum(len(r["s3_keys"]) for r in records)
    downloaded = 0

    for rec in records:
        for key in rec["s3_keys"]:
            local_path = download_root / key
            local_path.parent.mkdir(parents=True, exist_ok=True)
            if local_path.exists():
                downloaded += 1
                continue
            try:
                s3.download_file(Bucket=S3_ACCESS_POINT, Key=key, Filename=str(local_path))
                downloaded += 1
                if downloaded % 50 == 0:
                    print(f"  Downloaded {downloaded}/{total} files ...")
            except Exception as exc:
                print(f"  [ERROR] {key}: {exc}")

    print(f"  Download complete: {downloaded}/{total} files")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    default_csv_dir = os.environ.get(
        "BDSP_META_DIR",
        str(Path(__file__).resolve().parents[1]
            / "STELAR-private" / "pretrain" / "reve" / "metadata"),
    )
    p = argparse.ArgumentParser(
        description="Build a ~100-hour EEG download manifest from 5 BDSP sites.",
    )
    p.add_argument("--csv-dir", type=Path, default=Path(default_csv_dir),
                    help="Directory containing per-site metadata CSVs")
    p.add_argument("--output", required=True, type=Path, help="Output manifest JSON")
    p.add_argument("--target-hours", type=float, default=100.0)
    p.add_argument("--min-duration", type=float, default=60.0,
                    help="Min recording seconds (default: 60)")
    p.add_argument("--max-duration", type=float, default=1200.0,
                    help="Max recording seconds (default: 1200)")
    p.add_argument("--download", action="store_true",
                    help="Download from S3 (requires AWS creds)")
    p.add_argument("--download-root", type=Path, default=Path("data/raw/eeg"))
    p.add_argument("--dry-run", action="store_true",
                    help="Show what would be downloaded")
    return p


def main() -> None:
    args = build_parser().parse_args()

    print(f"Scanning metadata CSVs in {args.csv_dir} ...")
    candidates = collect_candidates(args.csv_dir, args.min_duration, args.max_duration)

    total_available = sum(len(v) for v in candidates.values())
    print(f"\nTotal candidates (unique subjects, "
          f"{args.min_duration}–{args.max_duration}s): {total_available}")

    print(f"\nSelecting stratified subset targeting {args.target_hours}h ...")
    selected = select_stratified(candidates, args.target_hours)

    summary = write_manifest(selected, args.output)
    print(f"\nManifest written to {args.output}")
    print(json.dumps(summary, indent=2))

    if args.download or args.dry_run:
        print(f"\n{'[DRY-RUN] ' if args.dry_run else ''}Downloading EDF files ...")
        download_from_s3(selected, args.download_root, dry_run=args.dry_run)


if __name__ == "__main__":
    main()

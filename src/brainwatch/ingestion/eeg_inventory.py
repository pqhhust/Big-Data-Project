from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


SERVICE_TASK_MAP: dict[str, dict[str, list[str]]] = {
    "S0001": {
        "LTM": ["cEEG"],
        "EMU": ["cEEG"],
        "Routine": ["rEEG"],
        "OR": ["OR"],
    },
    "S0002": {
        "LTM": ["cEEG", "EEG"],
        "Routine": ["rEEG"],
        "EMU": ["EMU"],
        "Faulkner": ["EEG"],
        "Fish": ["rEEG"],
    },
}


def resolve_task(site: str, service_name: str) -> list[str]:
    return SERVICE_TASK_MAP.get(site, {}).get(service_name, ["EEG"])


def parse_duration(row: dict[str, str]) -> float | None:
    raw = row.get("DurationInSeconds") or row.get("RecordingDuration") or ""
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def build_candidate_s3_keys(row: dict[str, str]) -> list[str]:
    site = row.get("SiteID") or row.get("InstituteID") or ""
    bids = row.get("BidsFolder") or ""
    session_id = row.get("SessionID") or ""
    if not (site and bids and session_id):
        return []
    service_name = row.get("ServiceName", "")
    keys = []
    for task in resolve_task(site, service_name):
        keys.append(
            f"EEG/bids/{site}/{bids}/ses-{session_id}/eeg/"
            f"{bids}_ses-{session_id}_task-{task}_eeg.edf"
        )
    return keys


def summarize_metadata(csv_paths: Iterable[str | Path], min_duration: float = 30.0) -> dict[str, Any]:
    site_rows: Counter[str] = Counter()
    site_subjects: dict[str, set[str]] = defaultdict(set)
    service_rows: Counter[str] = Counter()
    sex_counts: Counter[str] = Counter()
    total_duration_hours = 0.0
    short_sessions = 0
    missing_duration = 0
    candidate_keys = 0
    subjects: set[str] = set()
    sample_keys: list[str] = []

    for csv_path in csv_paths:
        with Path(csv_path).open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                site = row.get("SiteID") or row.get("InstituteID") or "UNKNOWN"
                bids = row.get("BidsFolder") or ""
                service_name = row.get("ServiceName") or "UNSPECIFIED"
                sex = row.get("SexDSC") or "UNKNOWN"
                duration = parse_duration(row)
                if duration is None:
                    missing_duration += 1
                elif duration < min_duration:
                    short_sessions += 1
                elif duration >= min_duration:
                    total_duration_hours += duration / 3600.0

                site_rows[site] += 1
                service_rows[service_name] += 1
                sex_counts[sex] += 1
                if bids:
                    subjects.add(bids)
                    site_subjects[site].add(bids)

                keys = build_candidate_s3_keys(row)
                candidate_keys += len(keys)
                if len(sample_keys) < 5:
                    sample_keys.extend(keys[: 5 - len(sample_keys)])

    return {
        "min_duration_seconds": min_duration,
        "total_rows": sum(site_rows.values()),
        "unique_subjects": len(subjects),
        "total_candidate_s3_keys": candidate_keys,
        "short_sessions_below_min_duration": short_sessions,
        "rows_missing_duration": missing_duration,
        "total_duration_hours_at_or_above_threshold": round(total_duration_hours, 2),
        "rows_by_site": dict(sorted(site_rows.items())),
        "subjects_by_site": {site: len(site_subjects[site]) for site in sorted(site_subjects)},
        "rows_by_service": dict(sorted(service_rows.items())),
        "sex_distribution": dict(sorted(sex_counts.items())),
        "sample_candidate_s3_keys": sample_keys,
    }


def write_summary(summary: dict[str, Any], output_path: str | Path) -> None:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)
        handle.write("\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Summarize EEG metadata CSVs.")
    parser.add_argument("--csv", action="append", required=True, help="Metadata CSV path")
    parser.add_argument("--output", required=True, help="Output JSON path")
    parser.add_argument("--min-duration", type=float, default=30.0, help="Minimum valid duration")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    summary = summarize_metadata(args.csv, min_duration=args.min_duration)
    write_summary(summary, args.output)
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

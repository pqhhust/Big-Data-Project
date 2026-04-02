from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Iterable

from brainwatch.ingestion.eeg_inventory import build_candidate_s3_keys, parse_duration


def select_subset(
    csv_paths: Iterable[str | Path],
    max_sessions: int,
    target_hours: float,
    min_duration: float = 300.0,
    max_duration: float = 5400.0,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    for csv_path in csv_paths:
        with Path(csv_path).open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                duration = parse_duration(row)
                if duration is None or duration < min_duration or duration > max_duration:
                    continue

                candidate_keys = build_candidate_s3_keys(row)
                if not candidate_keys:
                    continue

                candidates.append(
                    {
                        "site_id": row.get("SiteID") or row.get("InstituteID") or "UNKNOWN",
                        "subject_id": row.get("BidsFolder") or "",
                        "session_id": row.get("SessionID") or "",
                        "service_name": row.get("ServiceName") or "UNSPECIFIED",
                        "duration_seconds": duration,
                        "candidate_source_keys": candidate_keys,
                        "local_target_dir": "data/raw/eeg",
                    }
                )

    selected: list[dict[str, Any]] = []
    accumulated_hours = 0.0
    for record in sorted(candidates, key=lambda item: item["duration_seconds"]):
        selected.append(record)
        accumulated_hours += record["duration_seconds"] / 3600.0
        if len(selected) >= max_sessions or accumulated_hours >= target_hours:
            break
    return selected


def write_manifest(records: list[dict[str, Any]], output_path: str | Path) -> None:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "record_count": len(records),
        "estimated_total_hours": round(sum(r["duration_seconds"] for r in records) / 3600.0, 2),
        "records": records,
    }
    with target.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a small local EEG subset manifest.")
    parser.add_argument("--csv", action="append", required=True, help="Metadata CSV path")
    parser.add_argument("--output", required=True, help="Output manifest path")
    parser.add_argument("--max-sessions", type=int, default=12, help="Maximum selected sessions")
    parser.add_argument("--target-hours", type=float, default=8.0, help="Approximate target hours")
    parser.add_argument("--min-duration", type=float, default=300.0, help="Minimum session duration")
    parser.add_argument("--max-duration", type=float, default=5400.0, help="Maximum session duration")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    records = select_subset(
        csv_paths=args.csv,
        max_sessions=args.max_sessions,
        target_hours=args.target_hours,
        min_duration=args.min_duration,
        max_duration=args.max_duration,
    )
    write_manifest(records, args.output)
    print(
        json.dumps(
            {
                "record_count": len(records),
                "estimated_total_hours": round(sum(r["duration_seconds"] for r in records) / 3600.0, 2),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()

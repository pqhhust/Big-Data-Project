#!/usr/bin/env python3
"""Build Day 1 demo data: a 50-record mini manifest and synthetic EHR JSONL.

The workspace only contains a small local BIDS subset, so this script expands
an existing seed manifest into a larger demo manifest for report/demo purposes.
"""
from __future__ import annotations

import argparse
import json
import sys
from itertools import cycle
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from brainwatch.contracts.events import to_payload
from brainwatch.ingestion.ehr_normalizer import generate_ehr_from_manifest


DEFAULT_SEED_MANIFEST = Path("artifacts/week1/eeg_subset_manifest.json")
DEFAULT_MANIFEST = Path("artifacts/demo/mini_manifest.json")
DEFAULT_EHR = Path("artifacts/demo/synthetic_ehr.jsonl")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build Day 1 demo artifacts")
    parser.add_argument("--seed-manifest", type=Path, default=DEFAULT_SEED_MANIFEST)
    parser.add_argument("--output-manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-ehr", type=Path, default=DEFAULT_EHR)
    parser.add_argument("--target-records", type=int, default=50)
    parser.add_argument("--events-per-subject", type=int, default=5)
    return parser


def load_seed_manifest(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    records = payload.get("records") if isinstance(payload, dict) else None
    if not isinstance(records, list) or not records:
        raise ValueError(f"seed manifest has no records: {path}")
    return payload


def expand_manifest(seed_manifest: dict[str, Any], target_records: int) -> dict[str, Any]:
    seed_records = [record for record in seed_manifest["records"] if isinstance(record, dict)]
    expanded: list[dict[str, Any]] = []
    for index, record in zip(range(target_records), cycle(seed_records), strict=False):
        clone = dict(record)
        clone["subject_id"] = f"{record.get('subject_id', 'subject')}-demo-{index + 1:02d}"
        clone["session_id"] = f"{record.get('session_id', '1')}-{index + 1:02d}"
        clone["local_target_dir"] = "data/raw/eeg"
        expanded.append(clone)

    total_seconds = sum(float(record.get("duration_seconds", 0.0)) for record in expanded)
    return {
        "estimated_total_hours": round(total_seconds / 3600.0, 2),
        "record_count": len(expanded),
        "records": expanded,
    }


def main() -> None:
    args = build_parser().parse_args()
    seed_manifest = load_seed_manifest(args.seed_manifest)
    expanded_manifest = expand_manifest(seed_manifest, args.target_records)

    args.output_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.output_manifest.write_text(json.dumps(expanded_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    temp_manifest = args.output_manifest.with_suffix(".ehr.manifest.json")
    temp_manifest.write_text(json.dumps(expanded_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    events = generate_ehr_from_manifest(temp_manifest, events_per_subject=args.events_per_subject)
    temp_manifest.unlink(missing_ok=True)

    args.output_ehr.parent.mkdir(parents=True, exist_ok=True)
    with args.output_ehr.open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(to_payload(event), default=str))
            handle.write("\n")

    print(json.dumps({"manifest_records": len(expanded_manifest["records"]), "ehr_events": len(events)}, indent=2))


if __name__ == "__main__":
    main()
#!/usr/bin/env python3
"""Parse real EDF files with mne into measured bronze events.

Usage:
    python scripts/edf_to_bronze.py --bronze data/lake/bronze_real
"""

from __future__ import annotations

import argparse
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Parse EDF files into bronze events.")
    parser.add_argument("--edf-dir", default="data/raw/edf", help="Directory containing EDF files")
    parser.add_argument("--bronze", required=True, help="Bronze output directory")
    parser.add_argument("--chunk-seconds", type=float, default=1.0, help="Chunk duration in seconds")
    parser.add_argument("--max-files", type=int, default=0, help="Max files to process (0=unlimited)")
    return parser


def parse_edf_to_events(edf_path: Path, chunk_seconds: float = 1.0) -> list[dict]:
    """Parse a single EDF file into bronze EEG chunk events."""
    try:
        import mne
        mne.set_log_level("WARNING")

        raw = mne.io.read_raw_edf(str(edf_path), preload=False)
        patient_id = edf_path.stem.split("_")[0] if "_" in edf_path.stem else edf_path.stem
        session_id = f"S-{uuid.uuid4().hex[:8]}"
        site_id = "BDSP"

        duration = raw.times[-1]
        n_chunks = max(1, int(duration / chunk_seconds))

        events = []
        base_time = datetime.now(timezone.utc)

        for i in range(min(n_chunks, 1000)):  # cap at 1000 chunks per file
            event_time = base_time.replace(microsecond=0)
            events.append({
                "patient_id": patient_id,
                "session_id": session_id,
                "event_time": event_time.isoformat(),
                "site_id": site_id,
                "channel_count": len(raw.ch_names),
                "sampling_rate_hz": raw.info["sfreq"],
                "window_seconds": chunk_seconds,
                "source_uri": str(edf_path),
                "fingerprint": "",
            })

        logger.info("Parsed %s: %d channels, %.1fs duration → %d events",
                    edf_path.name, len(raw.ch_names), duration, len(events))
        return events

    except Exception as exc:
        logger.error("Failed to parse %s: %s", edf_path, exc)
        return []


def main() -> None:
    args = build_parser().parse_args()

    edf_dir = Path(args.edf_dir)
    bronze_dir = Path(args.bronze)
    bronze_dir.mkdir(parents=True, exist_ok=True)

    if not edf_dir.exists():
        logger.error("EDF directory not found: %s", edf_dir)
        return

    edf_files = sorted(edf_dir.glob("*.edf"))
    if args.max_files > 0:
        edf_files = edf_files[:args.max_files]

    logger.info("Processing %d EDF files from %s", len(edf_files), edf_dir)

    all_events = []
    for edf_path in edf_files:
        events = parse_edf_to_events(edf_path, args.chunk_seconds)
        all_events.extend(events)

    # Write as JSONL
    output_path = bronze_dir / "eeg_bronze.jsonl"
    with output_path.open("w", encoding="utf-8") as fh:
        for event in all_events:
            fh.write(json.dumps(event, default=str) + "\n")

    logger.info("Bronze events written: %d events → %s", len(all_events), output_path)

    # Summary
    summary = {
        "edf_files_processed": len(edf_files),
        "total_events": len(all_events),
        "output_path": str(output_path),
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

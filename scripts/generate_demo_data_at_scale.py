#!/usr/bin/env python3
"""Generate a multi-gigabyte synthetic bronze zone for demo / capstone runs.

Expands an existing download manifest by cloning subjects with new IDs,
splits each recording into ``window-seconds``-long EEG chunks, generates
matching EHR events, and writes them straight into the partitioned bronze
zone via :class:`brainwatch.ingestion.bronze_writer.BronzeWriter`.

Usage
-----
    python scripts/generate_demo_data_at_scale.py \
        --manifest artifacts/week2/download_manifest.json \
        --bronze data/lake/bronze \
        --target-gb 8 \
        --window-seconds 10 \
        --ehr-events-per-subject 12
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from brainwatch.contracts.events import EEGChunkEvent, EHREvent
from brainwatch.ingestion.bronze_writer import BronzeWriter


_EHR_TYPES = ("vital_signs", "lab_result", "medication", "critical_lab", "note")
_EHR_TYPE_WEIGHTS = (0.30, 0.25, 0.20, 0.10, 0.15)
_SOURCES = ("epic", "cerner")


def _bronze_size_bytes(bronze_root: Path) -> int:
    total = 0
    if not bronze_root.exists():
        return 0
    for dirpath, _, filenames in os.walk(bronze_root):
        for fn in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, fn))
            except OSError:
                pass
    return total


def _eeg_chunks_for_record(record: dict, window_seconds: float, clone_idx: int,
                           start: datetime) -> list[EEGChunkEvent]:
    duration = float(record.get("duration_seconds") or 600.0)
    n_chunks = max(1, int(duration / window_seconds))
    patient_id = f"{record['subject_id']}-c{clone_idx:03d}"
    session_id = f"{record['session_id']}-{clone_idx:03d}"
    source_uri = (record.get("s3_keys") or [""])[0]
    site_id = record["site_id"]
    events = []
    for i in range(n_chunks):
        et = (start + timedelta(seconds=i * window_seconds)).isoformat()
        events.append(EEGChunkEvent(
            patient_id=patient_id,
            session_id=session_id,
            event_time=et,
            site_id=site_id,
            channel_count=19,
            sampling_rate_hz=200.0,
            window_seconds=window_seconds,
            source_uri=source_uri,
        ))
    return events


def _ehr_events_for_subject(record: dict, clone_idx: int, count: int,
                            start: datetime, rng: random.Random) -> list[EHREvent]:
    patient_id = f"{record['subject_id']}-c{clone_idx:03d}"
    encounter_id = f"enc-{patient_id}"
    events = []
    for i in range(count):
        offset_min = rng.uniform(-30.0, 30.0)
        event_time = (start + timedelta(minutes=offset_min + i * 5)).isoformat()
        event_type = rng.choices(_EHR_TYPES, weights=_EHR_TYPE_WEIGHTS, k=1)[0]
        payload = {
            "code": rng.choice(["HR", "BP", "O2", "TEMP", "GLU", "K", "NA", "CR"]),
            "value": round(rng.uniform(50, 200), 2),
            "unit": rng.choice(["mmHg", "bpm", "mg/dL", "C"]),
        }
        events.append(EHREvent(
            patient_id=patient_id,
            encounter_id=encounter_id,
            event_time=event_time,
            event_type=event_type,
            source_system=rng.choice(_SOURCES),
            version=rng.randint(1, 3),
            payload=payload,
        ))
    return events


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--bronze", type=Path, default=Path("data/lake/bronze"))
    parser.add_argument("--target-gb", type=float, default=8.0)
    parser.add_argument("--window-seconds", type=float, default=10.0)
    parser.add_argument("--ehr-events-per-subject", type=int, default=12)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--checkpoint-every", type=int, default=50_000,
                        help="Print progress + size check after this many events")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    target_bytes = int(args.target_gb * 1024 ** 3)
    bronze_root = args.bronze
    bronze_root.mkdir(parents=True, exist_ok=True)

    with args.manifest.open() as f:
        manifest = json.load(f)
    records = manifest.get("records", [])
    if not records:
        print("manifest has no records", file=sys.stderr)
        return 1

    writer = BronzeWriter(bronze_root)
    base_start = datetime(2026, 5, 19, 8, 0, 0, tzinfo=timezone.utc)

    eeg_total = 0
    ehr_total = 0
    clone_idx = 0
    t0 = time.time()
    seen_size = 0

    while seen_size < target_bytes:
        for record in records:
            chunk_start = base_start + timedelta(minutes=clone_idx * 3)
            eeg_events = _eeg_chunks_for_record(record, args.window_seconds, clone_idx, chunk_start)
            for evt in eeg_events:
                writer.write_eeg(evt)
                eeg_total += 1

            ehr_events = _ehr_events_for_subject(record, clone_idx, args.ehr_events_per_subject, chunk_start, rng)
            for evt in ehr_events:
                writer.write_ehr(evt)
                ehr_total += 1

            if (eeg_total + ehr_total) % args.checkpoint_every == 0:
                seen_size = _bronze_size_bytes(bronze_root)
                elapsed = time.time() - t0
                print(f"[{elapsed:5.1f}s] eeg={eeg_total:>9,} ehr={ehr_total:>9,} "
                      f"bronze={seen_size / 1024**3:.2f} GiB clones={clone_idx + 1}")
                if seen_size >= target_bytes:
                    break
        clone_idx += 1

    elapsed = time.time() - t0
    final_size = _bronze_size_bytes(bronze_root)
    summary = {
        "eeg_events": eeg_total,
        "ehr_events": ehr_total,
        "bronze_bytes": final_size,
        "bronze_gib": round(final_size / 1024 ** 3, 3),
        "clones": clone_idx + 1,
        "elapsed_seconds": round(elapsed, 2),
        "events_per_second": round((eeg_total + ehr_total) / max(elapsed, 1e-6), 1),
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

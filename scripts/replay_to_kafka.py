#!/usr/bin/env python3
"""Unified replay simulator: publish EEG + EHR events to Kafka from a manifest.

Reads the download manifest, generates correlated EEG and synthetic EHR events,
and publishes them to their respective Kafka topics. Supports both batch and
time-proportional replay modes.

Usage
-----
  # Batch mode (fast, all events at once):
  python scripts/replay_to_kafka.py --manifest artifacts/week2/download_manifest.json

  # Replay mode (simulates real-time at 100x speed):
  python scripts/replay_to_kafka.py --manifest artifacts/week2/download_manifest.json --replay-speed 100

  # File fallback (no Kafka):
  python scripts/replay_to_kafka.py --manifest artifacts/week2/download_manifest.json --fallback
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from brainwatch.ingestion.eeg_producer import manifest_to_events, publish_events
from brainwatch.ingestion.ehr_normalizer import generate_ehr_from_manifest, publish_ehr_events


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Replay EEG + EHR events to Kafka.")
    p.add_argument("--manifest", required=True, type=Path)
    p.add_argument("--bootstrap-servers", default="localhost:9092")
    p.add_argument("--eeg-topic", default="eeg.raw")
    p.add_argument("--ehr-topic", default="ehr.updates")
    p.add_argument("--replay-speed", type=float, default=0.0,
                    help="0 = batch, N = Nx realtime speed")
    p.add_argument("--ehr-events-per-subject", type=int, default=5)
    p.add_argument("--fallback", action="store_true",
                    help="Use file-based fallback instead of Kafka")
    return p


def main() -> None:
    args = build_parser().parse_args()
    fallback = "artifacts/week2/kafka_fallback.jsonl" if args.fallback else None

    # --- EEG ---
    eeg_events = manifest_to_events(args.manifest)
    print(f"EEG: {len(eeg_events)} events from manifest")
    eeg_stats = publish_events(
        eeg_events,
        topic=args.eeg_topic,
        bootstrap_servers=args.bootstrap_servers,
        replay_speed=args.replay_speed,
        fallback_path=fallback,
    )
    print(f"  EEG publish: {json.dumps(eeg_stats)}")

    # --- EHR ---
    ehr_events = generate_ehr_from_manifest(args.manifest, args.ehr_events_per_subject)
    print(f"EHR: {len(ehr_events)} synthetic events")
    ehr_stats = publish_ehr_events(
        ehr_events,
        topic=args.ehr_topic,
        bootstrap_servers=args.bootstrap_servers,
        fallback_path=fallback,
    )
    print(f"  EHR publish: {json.dumps(ehr_stats)}")

    print("\nReplay complete.")


if __name__ == "__main__":
    main()

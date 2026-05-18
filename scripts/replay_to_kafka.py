#!/usr/bin/env python3
"""Unified replay simulator: publish EEG + EHR events to Kafka from a manifest.

Owner: **Quang-Hung** (lead) — orchestrates Kim-Quan's producers.

Usage
-----
    # Batch (fast, all events at once):
    python scripts/replay_to_kafka.py --manifest artifacts/week2/download_manifest.json

    # Replay at 100x realtime:
    python scripts/replay_to_kafka.py --manifest <m> --replay-speed 100

    # File fallback (no Kafka required):
    python scripts/replay_to_kafka.py --manifest <m> --fallback
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from brainwatch.ingestion.eeg_producer import manifest_to_events, publish_events
from brainwatch.ingestion.ehr_normalizer import generate_ehr_from_manifest, publish_ehr_events


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--manifest", required=True, type=Path)
    p.add_argument("--bootstrap-servers", default="localhost:9092")
    p.add_argument("--eeg-topic", default="eeg.raw")
    p.add_argument("--ehr-topic", default="ehr.updates")
    p.add_argument("--replay-speed", type=float, default=0.0,
                   help="0 = batch, N = Nx realtime")
    p.add_argument("--ehr-events-per-subject", type=int, default=5)
    p.add_argument("--fallback", action="store_true",
                   help="Use file-based fallback instead of Kafka")
    return p


def main() -> None:
    args = build_parser().parse_args()
    fallback = "artifacts/week2/kafka_fallback.jsonl" if args.fallback else None

    # 1. EEG events from manifest
    eeg_events = manifest_to_events(args.manifest)
    print(f"Generated {len(eeg_events)} EEG events from manifest")
    eeg_stats = publish_events(
        eeg_events,
        topic=args.eeg_topic,
        bootstrap_servers=args.bootstrap_servers,
        replay_speed=args.replay_speed,
        fallback_path=fallback
    )
    print(f"EEG stats: {eeg_stats}")

    # 2. Synthetic EHR events
    ehr_events = generate_ehr_from_manifest(
        args.manifest,
        events_per_subject=args.ehr_events_per_subject
    )
    print(f"Generated {len(ehr_events)} EHR events")
    ehr_stats = publish_ehr_events(
        ehr_events,
        topic=args.ehr_topic,
        bootstrap_servers=args.bootstrap_servers,
        replay_speed=args.replay_speed,
        fallback_path=fallback
    )
    print(f"EHR stats: {ehr_stats}")

    # 3. JSON summary
    summary = {
        "eeg": eeg_stats,
        "ehr": ehr_stats,
        "fallback_file": fallback
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from brainwatch.ingestion.eeg_replay import ReplayConfig, replay_manifest_to_kafka


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Replay EEG subset manifest as EEGChunkEvent JSON into Kafka topic eeg.raw."
    )
    parser.add_argument(
        "--manifest",
        default="artifacts/week1/eeg_subset_manifest.json",
        help="Path to subset manifest JSON",
    )
    parser.add_argument(
        "--settings",
        default=None,
        help="Optional YAML config (uses kafka.bootstrap_servers and kafka.topics.eeg_raw)",
    )
    parser.add_argument("--bootstrap-servers", default=None, help="Kafka bootstrap servers, e.g. localhost:9092")
    parser.add_argument("--topic", default=None, help="Kafka topic name (default: eeg.raw via --settings)")
    parser.add_argument("--channel-count", type=int, default=19)
    parser.add_argument("--sampling-rate-hz", type=float, default=256.0)
    parser.add_argument("--window-seconds", type=float, default=10.0)
    parser.add_argument(
        "--replay-speed",
        type=float,
        default=20.0,
        help="Speed factor vs real time (higher = faster)",
    )
    parser.add_argument("--max-events", type=int, default=None, help="Stop after emitting N events")
    parser.add_argument("--dry-run", action="store_true", help="Print events to stdout instead of Kafka")
    parser.add_argument(
        "--start-now",
        action="store_true",
        help="Use current UTC time as base event_time (default)",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    start_time = datetime.now(timezone.utc) if args.start_now else None

    # If user doesn't pass bootstrap/topic and also doesn't pass settings,
    # default to common local Kafka.
    bootstrap_servers = args.bootstrap_servers or ("localhost:9092" if args.settings is None else None)
    topic = args.topic or ("eeg.raw" if args.settings is None else None)

    config = ReplayConfig(
        manifest_path=Path(args.manifest),
        bootstrap_servers=bootstrap_servers or "",
        topic=topic or "",
        channel_count=args.channel_count,
        sampling_rate_hz=args.sampling_rate_hz,
        window_seconds=args.window_seconds,
        replay_speed=args.replay_speed,
        max_events=args.max_events,
        dry_run=args.dry_run,
        start_time=start_time,
    )

    sent = replay_manifest_to_kafka(config, settings_path=args.settings)
    print(f"sent_events={sent}")


if __name__ == "__main__":
    main()

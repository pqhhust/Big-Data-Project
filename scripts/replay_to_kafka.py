#!/usr/bin/env python3
"""Replay EEG/EHR events from a manifest to Kafka or file fallback.

Usage:
    # With Kafka (Docker):
    python scripts/replay_to_kafka.py \
        --manifest artifacts/week2/download_manifest.json \
        --bootstrap-servers localhost:9094

    # File fallback:
    python scripts/replay_to_kafka.py \
        --manifest artifacts/week2/download_manifest.json \
        --fallback
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Replay EEG/EHR events to Kafka or file fallback."
    )
    parser.add_argument(
        "--manifest",
        required=True,
        help="Path to the download manifest JSON",
    )
    parser.add_argument(
        "--bootstrap-servers",
        default="localhost:9094",
        help="Kafka bootstrap servers (default: localhost:9094)",
    )
    parser.add_argument(
        "--eeg-topic",
        default="eeg.raw",
        help="Kafka topic for EEG events (default: eeg.raw)",
    )
    parser.add_argument(
        "--ehr-topic",
        default="ehr.updates",
        help="Kafka topic for EHR events (default: ehr.updates)",
    )
    parser.add_argument(
        "--fallback",
        action="store_true",
        help="Use file fallback instead of Kafka",
    )
    parser.add_argument(
        "--fallback-dir",
        default="data/fallback",
        help="Output directory for file fallback (default: data/fallback)",
    )
    parser.add_argument(
        "--rate-limit",
        type=float,
        default=0.0,
        help="Delay between events in seconds (default: 0 = no delay)",
    )
    parser.add_argument(
        "--max-events",
        type=int,
        default=0,
        help="Maximum events to replay (default: 0 = unlimited)",
    )
    parser.add_argument(
        "--ehr-events",
        default=None,
        help="Path to EHR events JSONL file (optional)",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()

    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        logger.error("Manifest not found: %s", manifest_path)
        sys.exit(1)

    # Choose writer
    if args.fallback:
        from brainwatch.ingestion.writers import FileEventWriter
        writer = FileEventWriter(args.fallback_dir)
        logger.info("Using file fallback: %s", args.fallback_dir)
    else:
        try:
            from brainwatch.ingestion.writers import KafkaEventWriter
            writer = KafkaEventWriter(bootstrap_servers=args.bootstrap_servers)
            logger.info("Using Kafka: %s", args.bootstrap_servers)
        except ImportError:
            logger.warning("kafka-python not installed, falling back to file mode")
            from brainwatch.ingestion.writers import FileEventWriter
            writer = FileEventWriter(args.fallback_dir)

    # Setup DLQ
    from brainwatch.ingestion.dlq import DeadLetterQueue
    dlq = DeadLetterQueue(output_path=Path(args.fallback_dir) / "dlq.jsonl")

    # Produce EEG events
    from brainwatch.ingestion.producers import EEGProducer
    eeg_producer = EEGProducer(
        writer=writer,
        topic=args.eeg_topic,
        dlq=dlq,
    )

    logger.info("Replaying EEG events from manifest: %s", manifest_path)
    eeg_stats = eeg_producer.produce_from_manifest(manifest_path)
    logger.info("EEG replay complete: %s", eeg_stats)

    # Produce EHR events if available
    ehr_path = args.ehr_events or str(manifest_path.parent / "ehr_events.jsonl")
    if Path(ehr_path).exists():
        from brainwatch.ingestion.producers import EHRProducer
        ehr_producer = EHRProducer(
            writer=writer,
            topic=args.ehr_topic,
            dlq=dlq,
        )
        logger.info("Replaying EHR events from: %s", ehr_path)
        ehr_stats = ehr_producer.produce_from_jsonl(ehr_path)
        logger.info("EHR replay complete: %s", ehr_stats)

    # Summary
    summary = {
        "eeg": eeg_stats,
        "dlq_count": dlq.count(),
        "mode": "file_fallback" if args.fallback else "kafka",
    }
    print(json.dumps(summary, indent=2))

    writer.close()
    dlq.close()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Run full batch pipeline: Bronze → Silver → Gold.

Usage:
    python scripts/run_batch.py \
        --bronze data/lake/bronze_real \
        --silver data/lake/silver_real \
        --gold data/lake/gold_real
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run batch pipeline: Bronze → Silver → Gold.")
    parser.add_argument("--bronze", required=True, help="Bronze data directory")
    parser.add_argument("--silver", required=True, help="Silver output directory")
    parser.add_argument("--gold", required=True, help="Gold output directory")
    parser.add_argument("--local", action="store_true", default=True, help="Run with local Spark (default)")
    parser.add_argument("--master", default="local[*]", help="Spark master URL")
    return parser


def run_local_batch(bronze_path: str, silver_path: str, gold_path: str) -> dict:
    """Run batch pipeline using local Python (no Spark required)."""
    from brainwatch.processing.batch_bronze import load_eeg_jsonl, load_ehr_jsonl

    bronze_dir = Path(bronze_path)
    silver_dir = Path(silver_path)
    gold_dir = Path(gold_path)

    for d in [silver_dir, gold_dir]:
        d.mkdir(parents=True, exist_ok=True)

    stats = {}

    # Bronze EEG
    eeg_jsonl = bronze_dir / "eeg_bronze.jsonl"
    if eeg_jsonl.exists():
        eeg_records = load_eeg_jsonl(eeg_jsonl)
        stats["bronze_eeg_records"] = len(eeg_records)

        # Write silver
        silver_eeg_path = silver_dir / "eeg_silver.jsonl"
        with silver_eeg_path.open("w", encoding="utf-8") as fh:
            for r in eeg_records:
                fh.write(json.dumps(r, default=str) + "\n")
        stats["silver_eeg_records"] = len(eeg_records)
    else:
        logger.warning("No EEG bronze found at %s", eeg_jsonl)
        stats["bronze_eeg_records"] = 0

    # Bronze EHR
    ehr_jsonl = bronze_dir / "ehr_bronze.jsonl"
    if ehr_jsonl.exists():
        ehr_records = load_ehr_jsonl(ehr_jsonl)
        stats["bronze_ehr_records"] = len(ehr_records)

        silver_ehr_path = silver_dir / "ehr_silver.jsonl"
        with silver_ehr_path.open("w", encoding="utf-8") as fh:
            for r in ehr_records:
                fh.write(json.dumps(r, default=str) + "\n")
        stats["silver_ehr_records"] = len(ehr_records)
    else:
        logger.warning("No EHR bronze found at %s", ehr_jsonl)
        stats["bronze_ehr_records"] = 0

    # Gold — aggregation summary
    gold_summary_path = gold_dir / "batch_summary.json"
    with gold_summary_path.open("w", encoding="utf-8") as fh:
        json.dump(stats, fh, indent=2)

    stats["gold_output"] = str(gold_summary_path)
    return stats


def main() -> None:
    args = build_parser().parse_args()

    logger.info("Batch pipeline: %s → %s → %s", args.bronze, args.silver, args.gold)

    try:
        # Try Spark first
        from brainwatch.processing.spark_helpers import get_or_create_spark_session
        from brainwatch.processing.batch_silver import (
            transform_eeg_bronze_to_silver,
            transform_ehr_bronze_to_silver,
        )
        from brainwatch.processing.batch_gold import build_gold_joined_features

        spark = get_or_create_spark_session(app_name="BrainWatch-Batch", master=args.master)

        logger.info("Running batch pipeline with Spark")
        transform_eeg_bronze_to_silver(spark, args.bronze, args.silver + "/eeg")
        transform_ehr_bronze_to_silver(spark, args.bronze, args.silver + "/ehr")
        build_gold_joined_features(spark, args.silver + "/eeg", args.silver + "/ehr", args.gold)

        spark.stop()
        stats = {"mode": "spark", "status": "complete"}

    except ImportError:
        logger.info("PySpark not available — running local batch")
        stats = run_local_batch(args.bronze, args.silver, args.gold)
        stats["mode"] = "local"

    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Download real EDF files from BDSP S3.

Usage:
    export BDSP_CREDENTIALS=../credentials/rootkey.csv
    python scripts/download_real_edf.py --target-gb 17 --min-duration 600 --max-duration 3000
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download real EDF files from BDSP S3.")
    parser.add_argument("--target-gb", type=float, default=17.0, help="Target download size in GB")
    parser.add_argument("--min-duration", type=float, default=600.0, help="Minimum recording duration (seconds)")
    parser.add_argument("--max-duration", type=float, default=3000.0, help="Maximum recording duration (seconds)")
    parser.add_argument("--output-dir", default="data/raw/edf", help="Output directory for EDF files")
    parser.add_argument("--manifest", default=None, help="Optional manifest to limit downloads")
    parser.add_argument("--credentials", default=None, help="Path to BDSP credentials CSV")
    parser.add_argument("--dry-run", action="store_true", help="List files without downloading")
    return parser


def load_s3_credentials(cred_path: str | None) -> dict[str, str]:
    """Load AWS credentials from a rootkey CSV or environment."""
    if cred_path and Path(cred_path).exists():
        import csv
        with Path(cred_path).open("r") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                return {
                    "aws_access_key_id": row.get("AWSAccessKeyId", ""),
                    "aws_secret_access_key": row.get("AWSSecretKey", ""),
                }
    return {
        "aws_access_key_id": os.environ.get("AWS_ACCESS_KEY_ID", ""),
        "aws_secret_access_key": os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
    }


def main() -> None:
    args = build_parser().parse_args()

    cred_path = args.credentials or os.environ.get("BDSP_CREDENTIALS")
    credentials = load_s3_credentials(cred_path)

    if not credentials.get("aws_access_key_id"):
        logger.warning("No AWS credentials found — running in dry-run mode")
        args.dry_run = True

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Target: %.1f GB, duration range: [%.0f, %.0f] seconds",
                args.target_gb, args.min_duration, args.max_duration)

    if args.dry_run:
        logger.info("DRY RUN — no files will be downloaded")
        summary = {
            "mode": "dry_run",
            "target_gb": args.target_gb,
            "output_dir": str(output_dir),
            "min_duration": args.min_duration,
            "max_duration": args.max_duration,
        }
        print(json.dumps(summary, indent=2))
        return

    # Actual download logic would use boto3 here
    try:
        import boto3

        s3 = boto3.client(
            "s3",
            aws_access_key_id=credentials["aws_access_key_id"],
            aws_secret_access_key=credentials["aws_secret_access_key"],
        )

        downloaded_gb = 0.0
        downloaded_files = 0

        # List and download EDF files
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket="bdsp-public-dataset", Prefix="EEG/bids/"):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if not key.endswith(".edf"):
                    continue

                size_gb = obj["Size"] / (1024 ** 3)
                if downloaded_gb + size_gb > args.target_gb:
                    break

                local_path = output_dir / Path(key).name
                if not local_path.exists():
                    s3.download_file("bdsp-public-dataset", key, str(local_path))
                    downloaded_gb += size_gb
                    downloaded_files += 1
                    logger.info("Downloaded: %s (%.2f GB total)", key, downloaded_gb)

            if downloaded_gb >= args.target_gb:
                break

        logger.info("Download complete: %d files, %.2f GB", downloaded_files, downloaded_gb)

    except ImportError:
        logger.error("boto3 not installed — cannot download from S3")
        sys.exit(1)
    except Exception as exc:
        logger.error("Download failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()

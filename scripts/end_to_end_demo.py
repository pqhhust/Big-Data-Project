#!/usr/bin/env python3
"""End-to-end demo scaffold: replay -> bronze -> silver -> gold -> alerts."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_MANIFEST = Path("artifacts/demo/mini_manifest.json")
DEFAULT_BRONZE = Path("data/lake/bronze")
DEFAULT_SILVER = Path("data/lake/silver")
DEFAULT_GOLD = Path("data/lake/gold")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the BrainWatch demo end to end")
    parser.add_argument("--mode", choices=["local", "k8s"], default="local")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--bronze", type=Path, default=DEFAULT_BRONZE)
    parser.add_argument("--silver", type=Path, default=DEFAULT_SILVER)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--kafka", default="localhost:9094")
    parser.add_argument("--cassandra", default="localhost")
    parser.add_argument("--min-critical-alerts", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--batch-module", default="brainwatch.processing.silver_layer")
    parser.add_argument("--gold-module", default="brainwatch.processing.gold_layer")
    parser.add_argument("--alerts-export", type=Path, default=None)
    return parser


def _run_command(command: list[str]) -> None:
    subprocess.run(command, check=True)


def _count_parquet_files(root: Path) -> int:
    return sum(1 for path in root.rglob("*.parquet") if path.is_file())


def load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"manifest not found: {path}")

    with path.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)

    records = manifest.get("records")
    if not isinstance(records, list):
        raise ValueError("manifest must contain a records list")

    return manifest


def step_1_replay(args: argparse.Namespace) -> dict[str, int]:
    """Replay the manifest into Kafka."""
    load_manifest(args.manifest)

    if args.mode == "local":
        command = [
            sys.executable,
            "scripts/replay_to_kafka.py",
            "--manifest",
            str(args.manifest),
            "--bootstrap-servers",
            args.kafka,
            "--fallback",
        ]
    else:
        command = [
            sys.executable,
            "scripts/replay_to_kafka.py",
            "--manifest",
            str(args.manifest),
            "--bootstrap-servers",
            args.kafka,
        ]

    _run_command(command)
    return {"eeg_published": 0, "ehr_published": 0}


def step_2_wait_for_bronze(args: argparse.Namespace) -> int:
    """Wait until bronze parquet files are present."""
    deadline = time.time() + args.timeout

    while time.time() < deadline:
        parquet_count = _count_parquet_files(args.bronze)
        if parquet_count > 0:
            return parquet_count
        time.sleep(5)

    return _count_parquet_files(args.bronze)


def step_3_trigger_batch(args: argparse.Namespace) -> None:
    """Trigger the silver and gold batch jobs."""
    if args.mode == "local":
        _run_command([
            sys.executable,
            "-m",
            args.batch_module,
            "--bronze",
            str(args.bronze),
            "--silver",
            str(args.silver),
        ])
        _run_command([
            sys.executable,
            "-m",
            args.gold_module,
            "--silver",
            str(args.silver),
            "--gold",
            str(args.gold),
        ])
        return

    _run_command([
        "kubectl",
        "create",
        "job",
        "--from=cronjob/spark-batch",
        "spark-batch-manual",
    ])


def step_4_query_alerts(args: argparse.Namespace) -> list[dict[str, Any]]:
    """Query alerts for the demo summary."""
    if args.alerts_export is None or not args.alerts_export.exists():
        _ = args.cassandra
        return []

    if args.alerts_export.suffix.lower() == ".jsonl":
        rows: list[dict[str, Any]] = []
        with args.alerts_export.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows

    with args.alerts_export.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    return []


def step_5_summary(
    args: argparse.Namespace,
    replay_stats: dict[str, int],
    bronze_files: int,
    alerts: list[dict[str, Any]],
) -> None:
    severity_counts = Counter(alert.get("severity", "unknown") for alert in alerts)
    critical_count = severity_counts.get("critical", 0)

    print("[demo] replay_stats=", replay_stats)
    print("[demo] bronze_parquet_files=", bronze_files)
    print("[demo] manifest_records=", len(load_manifest(args.manifest)["records"]))
    print("[demo] severity_counts=", dict(severity_counts))

    top_alerts = sorted(
        alerts,
        key=lambda alert: float(alert.get("anomaly_score", 0.0)),
        reverse=True,
    )[:5]
    for alert in top_alerts:
        print(
            "[demo] alert",
            alert.get("patient_id"),
            alert.get("alert_time"),
            alert.get("severity"),
            alert.get("anomaly_score"),
        )

    if critical_count < args.min_critical_alerts:
        raise SystemExit(
            f"expected at least {args.min_critical_alerts} critical alerts, got {critical_count}"
        )


def main() -> None:
    args = build_parser().parse_args()
    started = time.time()
    print(f"[demo] mode={args.mode} manifest={args.manifest}")

    replay_stats = step_1_replay(args)
    bronze_files = step_2_wait_for_bronze(args)
    step_3_trigger_batch(args)
    alerts = step_4_query_alerts(args)
    step_5_summary(args, replay_stats, bronze_files, alerts)

    print(f"[demo] total_seconds={time.time() - started:.1f}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""End-to-end demo scaffold: replay -> bronze -> silver -> gold -> alerts."""

from __future__ import annotations

import argparse
import json
import shutil
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
    parser.add_argument("--cassandra-namespace", default="brainwatch")
    parser.add_argument("--cassandra-pod", default="cassandra-0")
    parser.add_argument("--min-critical-alerts", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--batch-module", default="brainwatch.processing.silver_layer")
    parser.add_argument("--gold-module", default="brainwatch.processing.gold_layer")
    parser.add_argument("--alerts-export", type=Path, default=None)
    return parser


def _run_command(command: list[str], *, capture_output: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, text=True, capture_output=capture_output)


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


def _load_json_records(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows

    if path.suffix.lower() == ".jsonl":
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                if isinstance(record, dict):
                    rows.append(record)
        return rows

    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    if isinstance(payload, list):
        return [record for record in payload if isinstance(record, dict)]

    if isinstance(payload, dict):
        records = payload.get("records")
        if isinstance(records, list):
            return [record for record in records if isinstance(record, dict)]

    return rows


def _parse_cqlsh_json_output(output: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in output.splitlines():
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            record = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            rows.append(record)
    return rows


def _query_alerts_from_cassandra(args: argparse.Namespace) -> list[dict[str, Any]]:
    query = (
        "PAGING OFF; "
        "SELECT JSON patient_id, session_id, alert_time, severity, "
        "anomaly_score, explanation FROM brainwatch.alerts;"
    )

    if args.mode == "k8s":
        command = [
            "kubectl",
            "-n",
            args.cassandra_namespace,
            "exec",
            args.cassandra_pod,
            "--",
            "cqlsh",
            "-e",
            query,
        ]
    else:
        cqlsh = shutil.which("cqlsh")
        if cqlsh is None:
            return []
        command = [cqlsh, args.cassandra, "9042", "-e", query]

    try:
        result = _run_command(command, capture_output=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return []
    return _parse_cqlsh_json_output(result.stdout)


def _query_alerts(args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.alerts_export is not None and args.alerts_export.exists():
        return _load_json_records(args.alerts_export)

    alerts = _query_alerts_from_cassandra(args)
    if alerts:
        return alerts

    if args.mode == "local":
        fallback_export = Path("artifacts/demo/alerts_export.jsonl")
        return _load_json_records(fallback_export)

    return []


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

    result = _run_command(command, capture_output=True)

    for line in reversed(result.stdout.splitlines()):
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return {
                "eeg_published": int(payload.get("eeg_published", 0)),
                "ehr_published": int(payload.get("ehr_published", 0)),
            }

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

    job_name = f"spark-batch-manual-{int(time.time())}"
    _run_command([
        "kubectl",
        "-n",
        args.cassandra_namespace,
        "create",
        "job",
        "--from=cronjob/spark-batch",
        job_name,
    ])

    _run_command([
        "kubectl",
        "-n",
        args.cassandra_namespace,
        "wait",
        "--for=condition=complete",
        f"job/{job_name}",
        f"--timeout={args.timeout}s",
    ])


def step_4_query_alerts(args: argparse.Namespace) -> list[dict[str, Any]]:
    """Query alerts for the demo summary."""
    if args.alerts_export is None and args.mode == "local":
        default_export = Path("artifacts/demo/alerts_export.jsonl")
        if default_export.exists():
            args.alerts_export = default_export

    return _query_alerts(args)


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
    print("[demo] alerts_total=", len(alerts))
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

#!/usr/bin/env python3
"""Cassandra → S3 exporter for the live Grafana dashboards.

Polls ``brainwatch.alerts`` every ``--interval`` seconds, writes the most
recent ``--limit`` rows as ``alerts_export.jsonl``, recomputes the per-panel
rollup JSONs, and pushes them to the public S3 bucket the dashboards read
from.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
from botocore.client import Config as BotoConfig
from cassandra.cluster import Cluster

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from brainwatch.analytics.rollups import (  # noqa: E402
    build_recent, build_score_histogram, build_severity_breakdown,
    build_summary, build_timeline, build_top_patients,
)


def _connect_cassandra(host: str):
    last_err = None
    for attempt in range(20):
        try:
            cluster = Cluster([host])
            session = cluster.connect("brainwatch")
            return cluster, session
        except Exception as e:
            last_err = e
            print(f"[exporter] cassandra connect attempt {attempt + 1} failed: {e}", file=sys.stderr)
            time.sleep(5)
    raise last_err


def _fetch_alerts(session, limit: int) -> list[dict]:
    rows = []
    # alerts is partitioned by patient_id with ALLOW FILTERING for the global scan.
    stmt = (
        "SELECT patient_id, alert_time, severity, anomaly_score, explanation "
        "FROM alerts LIMIT %d ALLOW FILTERING" % limit
    )
    for r in session.execute(stmt, timeout=15.0):
        rows.append({
            "patient_id":    r.patient_id,
            "alert_time":    r.alert_time.replace(tzinfo=timezone.utc).isoformat() if r.alert_time else "",
            "severity":      r.severity,
            "anomaly_score": float(r.anomaly_score) if r.anomaly_score is not None else 0.0,
            "explanation":   r.explanation or "",
        })
    return rows


def _upload(s3, bucket: str, key: str, body_bytes: bytes):
    s3.put_object(
        Bucket=bucket, Key=key, Body=body_bytes,
        CacheControl="no-cache,max-age=0",
        ContentType="application/json",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cassandra", default=os.environ.get("CASSANDRA_HOST", "cassandra-0.cassandra-svc.brainwatch.svc.cluster.local"))
    parser.add_argument("--bucket",    default=os.environ.get("S3_BUCKET", "brainwatch-dashboard-923884399064"))
    parser.add_argument("--limit",     type=int, default=5000)
    parser.add_argument("--interval",  type=float, default=3.0)
    args = parser.parse_args()

    cluster, session = _connect_cassandra(args.cassandra)
    s3 = boto3.client("s3", config=BotoConfig(retries={"max_attempts": 3}))
    print(f"[exporter] cassandra={args.cassandra}  bucket={args.bucket}  interval={args.interval}s")

    while True:
        t0 = time.time()
        try:
            rows = _fetch_alerts(session, args.limit)
        except Exception as e:
            print(f"[exporter] fetch error: {e}", file=sys.stderr)
            time.sleep(args.interval)
            continue

        jsonl = "\n".join(json.dumps(r) for r in rows) + "\n"
        artifacts = {
            "alerts_export.jsonl":  jsonl.encode("utf-8"),
            "summary.json":         json.dumps(build_summary(rows)).encode("utf-8"),
            "severity.json":        json.dumps(build_severity_breakdown(rows)).encode("utf-8"),
            "timeline.json":        json.dumps(build_timeline(rows), default=str).encode("utf-8"),
            "score_histogram.json": json.dumps(build_score_histogram(rows)).encode("utf-8"),
            "top_patients.json":    json.dumps(build_top_patients(rows), default=str).encode("utf-8"),
            "recent.json":          json.dumps(build_recent(rows), default=str).encode("utf-8"),
        }
        for key, body in artifacts.items():
            try:
                _upload(s3, args.bucket, key, body)
            except Exception as e:
                print(f"[exporter] upload {key} error: {e}", file=sys.stderr)

        elapsed = time.time() - t0
        print(f"[exporter] rows={len(rows):>5} elapsed={elapsed:.2f}s", flush=True)
        sleep_for = max(0.0, args.interval - elapsed)
        time.sleep(sleep_for)


if __name__ == "__main__":
    raise SystemExit(main())

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
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
from botocore.client import Config as BotoConfig
from cassandra.cluster import Cluster

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))


_SEV_ORDER = ["critical", "warning", "advisory", "normal", "suppressed"]
_RISK_WEIGHT = {"critical": 3, "warning": 2, "advisory": 1, "normal": 0, "suppressed": 0}


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


def _build_summary(rows: list[dict]) -> dict:
    by_sev = Counter(r["severity"] for r in rows)
    scores = [r["anomaly_score"] for r in rows]
    return {
        "generated_at":     datetime.now(timezone.utc).isoformat(),
        "total":            len(rows),
        "critical":         by_sev.get("critical", 0),
        "warning":          by_sev.get("warning", 0),
        "advisory":         by_sev.get("advisory", 0),
        "normal":           by_sev.get("normal", 0),
        "suppressed":       by_sev.get("suppressed", 0),
        "warning_advisory": by_sev.get("warning", 0) + by_sev.get("advisory", 0),
        "unique_patients":  len({r["patient_id"] for r in rows}),
        "mean_score":       round(sum(scores) / len(scores), 4) if scores else 0.0,
        "max_score":        round(max(scores), 4) if scores else 0.0,
    }


def _build_severity(rows: list[dict]) -> list[dict]:
    c = Counter(r["severity"] for r in rows)
    return [{"severity": s, "count": c.get(s, 0)} for s in _SEV_ORDER]


def _parse_dt(s: str):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _build_timeline(rows: list[dict], window_minutes: int = 60, bucket_seconds: int = 60) -> list[dict]:
    now = datetime.now(timezone.utc)
    floor_unix = int(now.timestamp() // bucket_seconds * bucket_seconds)
    earliest_unix = floor_unix - window_minutes * 60
    buckets: dict[int, Counter] = defaultdict(Counter)
    for r in rows:
        dt = _parse_dt(r["alert_time"])
        if dt is None:
            continue
        t = int(dt.timestamp())
        if t < earliest_unix:
            continue
        buckets[(t // bucket_seconds) * bucket_seconds][r["severity"]] += 1
    out = []
    t = earliest_unix
    while t <= floor_unix:
        b = buckets.get(t) or Counter()
        out.append({
            "t":          datetime.fromtimestamp(t, tz=timezone.utc).isoformat(),
            "critical":   b.get("critical", 0),
            "warning":    b.get("warning", 0),
            "advisory":   b.get("advisory", 0),
            "normal":     b.get("normal", 0),
            "suppressed": b.get("suppressed", 0),
        })
        t += bucket_seconds
    return out


def _build_score_histogram(rows: list[dict], bins: int = 20) -> list[dict]:
    counts = [0] * bins
    for r in rows:
        s = r["anomaly_score"]
        i = min(bins - 1, max(0, int(s * bins)))
        counts[i] += 1

    def band(score: float) -> str:
        if score >= 0.85: return "critical"
        if score >= 0.65: return "warning"
        if score >= 0.40: return "advisory"
        return "normal"

    return [
        {"bin_center": round((i + 0.5) / bins, 3),
         "count": counts[i],
         "severity_band": band(i / bins)}
        for i in range(bins)
    ]


def _build_top_patients(rows: list[dict], limit: int = 10) -> list[dict]:
    agg: dict[str, dict] = {}
    for r in rows:
        pid = r["patient_id"]
        row = agg.get(pid) or {"patient_id": pid, "total": 0,
                               "critical": 0, "warning": 0, "advisory": 0,
                               "max_score": 0.0, "latest": ""}
        row["total"] += 1
        if r["severity"] in row:
            row[r["severity"]] += 1
        if r["anomaly_score"] > row["max_score"]:
            row["max_score"] = round(r["anomaly_score"], 4)
        if r["alert_time"] > row["latest"]:
            row["latest"] = r["alert_time"]
        agg[pid] = row
    for row in agg.values():
        row["risk"] = (
            _RISK_WEIGHT["critical"] * row["critical"]
            + _RISK_WEIGHT["warning"] * row["warning"]
            + _RISK_WEIGHT["advisory"] * row["advisory"]
        )
    return sorted(agg.values(), key=lambda r: (r["risk"], r["max_score"]), reverse=True)[:limit]


def _build_recent(rows: list[dict], limit: int = 25) -> list[dict]:
    ranked = sorted(rows, key=lambda r: r["alert_time"], reverse=True)[:limit]
    out = []
    for r in ranked:
        out.append({
            "alert_time":    r["alert_time"],
            "patient_id":    r["patient_id"],
            "severity":      r["severity"],
            "anomaly_score": round(r["anomaly_score"], 3),
            "has_critical_lab": False,
            "n_eeg_chunks": None,
            "explanation":   r["explanation"],
        })
    return out


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
            "summary.json":         json.dumps(_build_summary(rows)).encode("utf-8"),
            "severity.json":        json.dumps(_build_severity(rows)).encode("utf-8"),
            "timeline.json":        json.dumps(_build_timeline(rows), default=str).encode("utf-8"),
            "score_histogram.json": json.dumps(_build_score_histogram(rows)).encode("utf-8"),
            "top_patients.json":    json.dumps(_build_top_patients(rows), default=str).encode("utf-8"),
            "recent.json":          json.dumps(_build_recent(rows), default=str).encode("utf-8"),
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

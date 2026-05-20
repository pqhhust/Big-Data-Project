#!/usr/bin/env python3
"""Roll the live alerts JSONL up into per-panel JSON endpoints for Grafana.

Reads ``dashboard/public/alerts_export.jsonl`` (continuously appended to
by ``live_alerts_feeder.py``) and emits, alongside it::

    summary.json         — total + counts by severity + mean/max score
    severity.json        — [{severity, count}] for the bar/pie panel
    timeline.json        — [{t, critical, warning, advisory, normal}] bucketed by minute
    score_histogram.json — [{bin_center, count, severity_band}]
    top_patients.json    — top patients by weighted risk
    recent.json          — last N alerts, sorted by alert_time desc
"""
from __future__ import annotations

import argparse
import json
import math
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


_SEV_ORDER = ["critical", "warning", "advisory", "normal", "suppressed"]
_RISK_WEIGHT = {"critical": 3, "warning": 2, "advisory": 1, "normal": 0, "suppressed": 0}


def _read_alerts(path: Path) -> list[dict]:
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text().splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            rows.append(json.loads(s))
        except json.JSONDecodeError:
            continue
    return rows


def _parse_dt(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def build_summary(rows: list[dict]) -> dict:
    by_sev = Counter(r.get("severity", "normal") for r in rows)
    scores = [float(r["anomaly_score"]) for r in rows if isinstance(r.get("anomaly_score"), (int, float))]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total":              len(rows),
        "critical":           by_sev.get("critical", 0),
        "warning":            by_sev.get("warning", 0),
        "advisory":           by_sev.get("advisory", 0),
        "normal":             by_sev.get("normal", 0),
        "suppressed":         by_sev.get("suppressed", 0),
        "warning_advisory":   by_sev.get("warning", 0) + by_sev.get("advisory", 0),
        "unique_patients":    len({r.get("patient_id") for r in rows if r.get("patient_id")}),
        "mean_score":         round(sum(scores) / len(scores), 4) if scores else 0.0,
        "max_score":          round(max(scores), 4) if scores else 0.0,
    }


def build_severity_breakdown(rows: list[dict]) -> list[dict]:
    by_sev = Counter(r.get("severity", "normal") for r in rows)
    return [{"severity": sev, "count": by_sev.get(sev, 0)} for sev in _SEV_ORDER]


def build_timeline(rows: list[dict], window_minutes: int = 60, bucket_seconds: int = 60) -> list[dict]:
    """Bucket the last `window_minutes` of alerts by `bucket_seconds`."""
    now = datetime.now(timezone.utc)
    floor_unix = int(now.timestamp() // bucket_seconds * bucket_seconds)
    earliest_unix = floor_unix - (window_minutes * 60)

    buckets: dict[int, Counter] = defaultdict(Counter)
    for r in rows:
        dt = _parse_dt(str(r.get("alert_time", "")))
        if dt is None:
            continue
        t = int(dt.timestamp())
        if t < earliest_unix:
            continue
        b = (t // bucket_seconds) * bucket_seconds
        buckets[b][r.get("severity", "normal")] += 1

    # Fill in zero buckets so the time series is continuous
    out = []
    t = earliest_unix
    while t <= floor_unix:
        b = buckets.get(t) or Counter()
        out.append({
            "t":        datetime.fromtimestamp(t, tz=timezone.utc).isoformat(),
            "critical": b.get("critical", 0),
            "warning":  b.get("warning", 0),
            "advisory": b.get("advisory", 0),
            "normal":   b.get("normal", 0),
            "suppressed": b.get("suppressed", 0),
        })
        t += bucket_seconds
    return out


def build_score_histogram(rows: list[dict], bins: int = 20) -> list[dict]:
    counts = [0] * bins
    for r in rows:
        s = r.get("anomaly_score")
        if not isinstance(s, (int, float)):
            continue
        if math.isnan(s):
            continue
        i = min(bins - 1, max(0, int(s * bins)))
        counts[i] += 1

    def band(score):
        if score >= 0.85: return "critical"
        if score >= 0.65: return "warning"
        if score >= 0.40: return "advisory"
        return "normal"

    return [
        {
            "bin_center": round((i + 0.5) / bins, 3),
            "count": counts[i],
            "severity_band": band(i / bins),
        }
        for i in range(bins)
    ]


def build_top_patients(rows: list[dict], limit: int = 10) -> list[dict]:
    agg: dict[str, dict] = {}
    for r in rows:
        pid = r.get("patient_id")
        if not pid:
            continue
        row = agg.get(pid) or {
            "patient_id": pid, "total": 0,
            "critical": 0, "warning": 0, "advisory": 0, "normal": 0,
            "max_score": 0.0, "latest": "",
        }
        sev = r.get("severity", "normal")
        row["total"] += 1
        if sev in row:
            row[sev] += 1
        s = float(r.get("anomaly_score") or 0)
        if s > row["max_score"]:
            row["max_score"] = round(s, 4)
        at = r.get("alert_time") or ""
        if at > row["latest"]:
            row["latest"] = at
        agg[pid] = row

    for row in agg.values():
        row["risk"] = (
            _RISK_WEIGHT["critical"] * row.get("critical", 0)
            + _RISK_WEIGHT["warning"] * row.get("warning", 0)
            + _RISK_WEIGHT["advisory"] * row.get("advisory", 0)
        )

    ranked = sorted(agg.values(), key=lambda r: (r["risk"], r["max_score"]), reverse=True)
    return ranked[:limit]


def build_recent(rows: list[dict], limit: int = 25) -> list[dict]:
    ranked = sorted(rows, key=lambda r: r.get("alert_time", ""), reverse=True)
    out = []
    for r in ranked[:limit]:
        out.append({
            "alert_time":   r.get("alert_time"),
            "patient_id":   r.get("patient_id"),
            "severity":     r.get("severity"),
            "anomaly_score": round(float(r.get("anomaly_score") or 0), 3),
            "has_critical_lab": bool(r.get("has_critical_lab")),
            "n_eeg_chunks": r.get("n_eeg_chunks"),
            "explanation":  r.get("explanation"),
        })
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alerts", type=Path, default=Path("dashboard/public/alerts_export.jsonl"))
    parser.add_argument("--out", type=Path, default=Path("dashboard/public"))
    parser.add_argument("--watch-interval", type=float, default=0,
                        help="If >0, recompute every N seconds in a loop")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)

    def _once():
        rows = _read_alerts(args.alerts)
        artifacts = {
            "summary.json":          build_summary(rows),
            "severity.json":         build_severity_breakdown(rows),
            "timeline.json":         build_timeline(rows),
            "score_histogram.json":  build_score_histogram(rows),
            "top_patients.json":     build_top_patients(rows),
            "recent.json":           build_recent(rows),
        }
        for name, data in artifacts.items():
            (args.out / name).write_text(json.dumps(data, default=str))
        return len(rows)

    if args.watch_interval <= 0:
        n = _once()
        print(json.dumps({"rows": n, "out": str(args.out)}))
        return 0

    print(f"[rollups] watching {args.alerts} every {args.watch_interval}s")
    try:
        while True:
            t0 = time.time()
            n = _once()
            print(f"[rollups] rows={n} elapsed={time.time() - t0:.2f}s", flush=True)
            time.sleep(args.watch_interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

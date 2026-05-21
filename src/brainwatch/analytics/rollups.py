"""Canonical dashboard rollup builders.

Single source of truth for the per-panel aggregations consumed by the Grafana
Infinity datasource. Both the local rollup watcher (``build_dashboard_rollups``)
and the in-cluster Cassandra exporter (``cassandra_to_s3_exporter``) import
these so the dashboard sees identical shapes regardless of the alert source.

Each builder takes a list of alert dicts with at least::

    {"patient_id", "severity", "anomaly_score", "alert_time", "explanation"}
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone

SEVERITY_ORDER = ["critical", "warning", "advisory", "normal", "suppressed"]
RISK_WEIGHT = {"critical": 3, "warning": 2, "advisory": 1, "normal": 0, "suppressed": 0}


def parse_dt(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def build_summary(rows: list[dict]) -> dict:
    by_sev = Counter(r.get("severity", "normal") for r in rows)
    scores = [float(r["anomaly_score"]) for r in rows
              if isinstance(r.get("anomaly_score"), (int, float))]
    return {
        "generated_at":     datetime.now(timezone.utc).isoformat(),
        "total":            len(rows),
        "critical":         by_sev.get("critical", 0),
        "warning":          by_sev.get("warning", 0),
        "advisory":         by_sev.get("advisory", 0),
        "normal":           by_sev.get("normal", 0),
        "suppressed":       by_sev.get("suppressed", 0),
        "warning_advisory": by_sev.get("warning", 0) + by_sev.get("advisory", 0),
        "unique_patients":  len({r.get("patient_id") for r in rows if r.get("patient_id")}),
        "mean_score":       round(sum(scores) / len(scores), 4) if scores else 0.0,
        "max_score":        round(max(scores), 4) if scores else 0.0,
    }


def build_severity_breakdown(rows: list[dict]) -> list[dict]:
    by_sev = Counter(r.get("severity", "normal") for r in rows)
    return [{"severity": sev, "count": by_sev.get(sev, 0)} for sev in SEVERITY_ORDER]


def build_timeline(rows: list[dict], window_minutes: int = 60, bucket_seconds: int = 60) -> list[dict]:
    now = datetime.now(timezone.utc)
    floor_unix = int(now.timestamp() // bucket_seconds * bucket_seconds)
    earliest_unix = floor_unix - (window_minutes * 60)

    buckets: dict[int, Counter] = defaultdict(Counter)
    for r in rows:
        dt = parse_dt(r.get("alert_time", ""))
        if dt is None:
            continue
        t = int(dt.timestamp())
        if t < earliest_unix:
            continue
        buckets[(t // bucket_seconds) * bucket_seconds][r.get("severity", "normal")] += 1

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


def _band(score: float) -> str:
    if score >= 0.85:
        return "critical"
    if score >= 0.65:
        return "warning"
    if score >= 0.40:
        return "advisory"
    return "normal"


def build_score_histogram(rows: list[dict], bins: int = 20) -> list[dict]:
    counts = [0] * bins
    for r in rows:
        s = r.get("anomaly_score")
        if not isinstance(s, (int, float)):
            continue
        idx = min(bins - 1, max(0, int(s * bins)))
        counts[idx] += 1
    return [
        {"bin_center": round((i + 0.5) / bins, 3), "count": counts[i], "severity_band": _band(i / bins)}
        for i in range(bins)
    ]


def build_top_patients(rows: list[dict], limit: int = 10) -> list[dict]:
    agg: dict[str, dict] = {}
    for r in rows:
        pid = r.get("patient_id")
        if not pid:
            continue
        row = agg.get(pid) or {"patient_id": pid, "total": 0,
                               "critical": 0, "warning": 0, "advisory": 0, "normal": 0,
                               "max_score": 0.0, "latest": ""}
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
        row["risk"] = (RISK_WEIGHT["critical"] * row.get("critical", 0)
                       + RISK_WEIGHT["warning"] * row.get("warning", 0)
                       + RISK_WEIGHT["advisory"] * row.get("advisory", 0))
    return sorted(agg.values(), key=lambda r: (r["risk"], r["max_score"]), reverse=True)[:limit]


def build_recent(rows: list[dict], limit: int = 25) -> list[dict]:
    ranked = sorted(rows, key=lambda r: r.get("alert_time", ""), reverse=True)[:limit]
    return [
        {
            "alert_time":       r.get("alert_time"),
            "patient_id":       r.get("patient_id"),
            "severity":         r.get("severity"),
            "anomaly_score":    round(float(r.get("anomaly_score") or 0), 3),
            "has_critical_lab": bool(r.get("has_critical_lab")),
            "n_eeg_chunks":     r.get("n_eeg_chunks"),
            "explanation":      r.get("explanation"),
        }
        for r in ranked
    ]

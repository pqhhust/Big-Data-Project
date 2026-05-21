"""Tests for the pure rollup builders in scripts/build_dashboard_rollups.py."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from scripts.build_dashboard_rollups import (
    build_summary, build_severity_breakdown, build_timeline,
    build_score_histogram, build_top_patients, build_recent,
)


def _alert(pid, sev, score, t=None):
    return {
        "patient_id": pid, "severity": sev, "anomaly_score": score,
        "alert_time": (t or datetime(2026, 5, 19, 10, 0, tzinfo=timezone.utc)).isoformat(),
        "explanation": "x",
    }


SAMPLE = [
    _alert("p1", "critical", 0.92),
    _alert("p1", "warning", 0.70),
    _alert("p2", "advisory", 0.50),
    _alert("p3", "normal", 0.10),
    _alert("p3", "critical", 0.88),
]


def test_summary_counts_and_stats():
    s = build_summary(SAMPLE)
    assert s["total"] == 5
    assert s["critical"] == 2
    assert s["warning"] == 1
    assert s["advisory"] == 1
    assert s["normal"] == 1
    assert s["warning_advisory"] == 2
    assert s["unique_patients"] == 3
    assert 0.0 <= s["mean_score"] <= 1.0
    assert s["max_score"] == 0.92


def test_summary_empty_is_safe():
    s = build_summary([])
    assert s["total"] == 0
    assert s["mean_score"] == 0.0
    assert s["max_score"] == 0.0
    assert s["unique_patients"] == 0


def test_severity_breakdown_order_and_counts():
    rows = build_severity_breakdown(SAMPLE)
    order = [r["severity"] for r in rows]
    assert order == ["critical", "warning", "advisory", "normal", "suppressed"]
    by = {r["severity"]: r["count"] for r in rows}
    assert by["critical"] == 2
    assert by["suppressed"] == 0


def test_score_histogram_bins_sum_to_total():
    hist = build_score_histogram(SAMPLE, bins=10)
    assert len(hist) == 10
    assert sum(h["count"] for h in hist) == len(SAMPLE)


def test_score_histogram_band_labels():
    hist = build_score_histogram(SAMPLE, bins=20)
    bands = {h["severity_band"] for h in hist}
    assert bands <= {"critical", "warning", "advisory", "normal"}


def test_top_patients_ranked_by_risk():
    top = build_top_patients(SAMPLE, limit=3)
    # p1 (1 crit + 1 warn = 3+2=5) and p3 (1 crit = 3) outrank p2 (1 adv = 1)
    assert top[0]["patient_id"] in ("p1", "p3")
    assert top[0]["risk"] >= top[-1]["risk"]


def test_top_patients_limit_respected():
    assert len(build_top_patients(SAMPLE, limit=2)) == 2


def test_recent_sorted_desc_and_limited():
    base = datetime(2026, 5, 19, 10, 0, tzinfo=timezone.utc)
    rows = [_alert("p", "normal", 0.1, base + timedelta(minutes=i)) for i in range(30)]
    recent = build_recent(rows, limit=10)
    assert len(recent) == 10
    times = [r["alert_time"] for r in recent]
    assert times == sorted(times, reverse=True)


def test_timeline_buckets_are_continuous_and_zero_filled():
    base = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    rows = [_alert("p", "critical", 0.9, base - timedelta(minutes=5))]
    tl = build_timeline(rows, window_minutes=10, bucket_seconds=60)
    # 10-min window at 60s buckets → 11 points, continuous
    assert len(tl) >= 10
    assert all(set(("t", "critical", "warning", "advisory", "normal")) <= set(p) for p in tl)
    assert sum(p["critical"] for p in tl) == 1


def test_timeline_ignores_unparseable_times():
    rows = [{"patient_id": "p", "severity": "critical", "anomaly_score": 0.9, "alert_time": "not-a-date"}]
    tl = build_timeline(rows)
    assert sum(p["critical"] for p in tl) == 0

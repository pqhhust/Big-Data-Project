"""Boundary + property tests for the anomaly scoring rules.

These lock the exact thresholds the speed layer and dashboards depend on.
"""
from __future__ import annotations

import pytest

from brainwatch.serving.anomaly_rules import (
    classify_anomaly, classify_v2, compute_anomaly_score,
)


# ── compute_anomaly_score ──────────────────────────────────────────────
def test_score_all_zero_features_is_zero():
    assert compute_anomaly_score({"signal_quality_score": 1.0}) == 0.0


def test_score_is_bounded_and_hits_formula_max():
    hi = compute_anomaly_score({
        "eeg_chunk_count": 10_000, "signal_quality_score": 0.0,
        "has_critical_lab": True, "n_medication_changes_24h": 100,
    })
    # max = 0.30*1 + 0.25*1 + 0.30*0.6 + 0.15*1 = 0.88 (critical_term caps at 0.18)
    assert hi == pytest.approx(0.88)
    assert 0.0 <= hi <= 1.0


def test_score_missing_quality_defaults_to_perfect():
    # signal_quality_score defaults to 1.0 → quality_term 0
    assert compute_anomaly_score({"eeg_chunk_count": 0}) == 0.0


def test_score_critical_lab_adds_exactly_point_three_times_point_six():
    base = compute_anomaly_score({"signal_quality_score": 1.0})
    with_lab = compute_anomaly_score({"signal_quality_score": 1.0, "has_critical_lab": True})
    assert round(with_lab - base, 6) == round(0.30 * 0.6, 6)


def test_score_chunk_term_saturates_at_60():
    a = compute_anomaly_score({"eeg_chunk_count": 60, "signal_quality_score": 1.0})
    b = compute_anomaly_score({"eeg_chunk_count": 600, "signal_quality_score": 1.0})
    assert a == b == pytest.approx(0.30)


def test_score_meds_term_saturates_at_5():
    a = compute_anomaly_score({"signal_quality_score": 1.0, "n_medication_changes_24h": 5})
    b = compute_anomaly_score({"signal_quality_score": 1.0, "n_medication_changes_24h": 50})
    assert a == b == pytest.approx(0.15)


def test_score_monotonic_in_chunk_count():
    prev = -1.0
    for n in (0, 10, 20, 40, 60):
        s = compute_anomaly_score({"eeg_chunk_count": n, "signal_quality_score": 1.0})
        assert s >= prev
        prev = s


# ── classify_v2 ────────────────────────────────────────────────────────
@pytest.mark.parametrize("score,expected", [
    (0.86, "critical"),
    (0.85, "critical"),
    (0.84, "warning"),
    (0.65, "warning"),
    (0.64, "advisory"),
    (0.40, "advisory"),
    (0.39, "normal"),
    (0.0,  "normal"),
])
def test_classify_v2_thresholds(score, expected):
    assert classify_v2(score).severity == expected


def test_classify_v2_critical_lab_escalates_at_point_six():
    # critical-lab boost only fires at score >= 0.6; below that the normal
    # score ladder applies (0.59 → advisory, since >= 0.40)
    assert classify_v2(0.60, has_critical_lab=True).severity == "critical"
    assert classify_v2(0.59, has_critical_lab=True).severity == "advisory"
    assert classify_v2(0.39, has_critical_lab=True).severity == "normal"


def test_classify_v2_returns_explanation():
    d = classify_v2(0.9)
    assert d.severity == "critical"
    assert "0.9" in d.explanation or "0.90" in d.explanation


# ── classify_anomaly (v1) ──────────────────────────────────────────────
@pytest.mark.parametrize("quality,score,expected", [
    (0.29, 0.99, "suppressed"),
    (0.30, 0.85, "critical"),
    (0.50, 0.60, "warning"),
    (0.50, 0.59, "normal"),
])
def test_classify_v1_thresholds(quality, score, expected):
    assert classify_anomaly(score, quality).severity == expected


def test_v1_suppressed_takes_priority_over_high_score():
    # low quality wins even with a critical-level score
    assert classify_anomaly(0.99, 0.1).severity == "suppressed"

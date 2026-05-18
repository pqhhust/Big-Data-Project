"""Tests for anomaly classification rules."""
from brainwatch.serving.anomaly_rules import (
    classify_anomaly,
    compute_anomaly_score,
    classify_v2,
    AlertDecision
)


def test_low_quality_signal_suppresses_alert() -> None:
    """Signal quality < 0.3 suppresses alerts regardless of score."""
    decision = classify_anomaly(anomaly_score=0.95, signal_quality_score=0.2)
    assert decision.severity == "suppressed"


def test_high_anomaly_score_becomes_critical() -> None:
    """Anomaly score >= 0.85 with good signal -> critical."""
    decision = classify_anomaly(anomaly_score=0.9, signal_quality_score=0.8)
    assert decision.severity == "critical"


def test_mid_anomaly_score_becomes_warning() -> None:
    """Anomaly score 0.6-0.85 -> warning."""
    decision = classify_anomaly(anomaly_score=0.7, signal_quality_score=0.8)
    assert decision.severity == "warning"


def test_normal_score_returns_normal() -> None:
    """Anomaly score < 0.6 -> normal."""
    decision = classify_anomaly(anomaly_score=0.4, signal_quality_score=0.8)
    assert decision.severity == "normal"


def test_compute_anomaly_score_returns_0_to_1() -> None:
    """Score should be bounded in [0, 1]."""
    # All zeros -> score should be low
    score = compute_anomaly_score({})
    assert 0.0 <= score <= 1.0

    # Max values -> score should be bounded at 1.0
    max_features = {
        "eeg_chunk_count": 1000,
        "signal_quality_score": 0.0,
        "has_critical_lab": True,
        "n_medication_changes_24h": 100
    }
    score = compute_anomaly_score(max_features)
    assert 0.0 <= score <= 1.0


def test_classify_v2_critical_threshold() -> None:
    """Score >= 0.85 -> critical."""
    decision = classify_v2(score=0.90)
    assert decision.severity == "critical"


def test_classify_v2_warning_threshold() -> None:
    """Score 0.65-0.85 -> warning."""
    decision = classify_v2(score=0.70)
    assert decision.severity == "warning"


def test_classify_v2_advisory_threshold() -> None:
    """Score 0.40-0.65 -> advisory."""
    decision = classify_v2(score=0.50)
    assert decision.severity == "advisory"


def test_classify_v2_normal_threshold() -> None:
    """Score < 0.40 -> normal."""
    decision = classify_v2(score=0.30)
    assert decision.severity == "normal"


def test_classify_v2_critical_lab_boost() -> None:
    """has_critical_lab=True boosts to critical at lower threshold."""
    # At 0.60 with critical_lab -> critical
    decision = classify_v2(score=0.60, has_critical_lab=True)
    assert decision.severity == "critical"
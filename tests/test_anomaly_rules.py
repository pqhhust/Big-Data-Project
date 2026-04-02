from brainwatch.serving.anomaly_rules import classify_anomaly


def test_low_quality_signal_suppresses_alert() -> None:
    decision = classify_anomaly(anomaly_score=0.95, signal_quality_score=0.2)
    assert decision.severity == "suppressed"


def test_high_anomaly_score_becomes_critical() -> None:
    decision = classify_anomaly(anomaly_score=0.9, signal_quality_score=0.8)
    assert decision.severity == "critical"


def test_mid_anomaly_score_becomes_warning() -> None:
    decision = classify_anomaly(anomaly_score=0.7, signal_quality_score=0.8)
    assert decision.severity == "warning"

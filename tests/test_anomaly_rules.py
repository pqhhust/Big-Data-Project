from brainwatch.serving.anomaly_rules import classify_anomaly, evaluate_all_rules, detect_flatline, detect_electrode_disconnect


def test_low_quality_signal_suppresses_alert() -> None:
    decision = classify_anomaly(anomaly_score=0.95, signal_quality_score=0.2)
    assert decision.severity == "suppressed"


def test_high_anomaly_score_becomes_critical() -> None:
    decision = classify_anomaly(anomaly_score=0.9, signal_quality_score=0.8)
    assert decision.severity == "critical"


def test_mid_anomaly_score_becomes_warning() -> None:
    decision = classify_anomaly(anomaly_score=0.7, signal_quality_score=0.8)
    assert decision.severity == "warning"


def test_normal_score() -> None:
    decision = classify_anomaly(anomaly_score=0.3, signal_quality_score=0.8)
    assert decision.severity == "normal"


def test_flatline_detection() -> None:
    result = detect_flatline(0.0001)
    assert result is not None
    assert result.severity == "critical"


def test_flatline_not_triggered() -> None:
    result = detect_flatline(1.0)
    assert result is None


def test_electrode_disconnect() -> None:
    result = detect_electrode_disconnect(10)
    assert result is not None
    assert result.severity == "warning"


def test_electrode_ok() -> None:
    result = detect_electrode_disconnect(21)
    assert result is None


def test_evaluate_all_rules_critical() -> None:
    decision = evaluate_all_rules(
        anomaly_score=0.9,
        signal_quality_score=0.8,
        signal_std=0.5,
        channel_count=21,
    )
    assert decision.severity == "critical"
    assert len(decision.triggered_rules) > 0


def test_evaluate_all_rules_normal() -> None:
    decision = evaluate_all_rules(
        anomaly_score=0.1,
        signal_quality_score=0.9,
        signal_std=1.0,
        channel_count=21,
    )
    assert decision.severity == "normal"

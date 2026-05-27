"""Anomaly classification rules for the BrainWatch serving layer.

Provides rule-based anomaly detection including:
- Score-based severity classification
- Flatline detection
- Electrode disconnect detection
- Seizure pattern heuristic
- Rule chaining
- Configurable thresholds
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class AlertDecision:
    """Result of anomaly classification."""

    severity: str
    explanation: str
    triggered_rules: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AnomalyThresholds:
    """Configurable thresholds for anomaly detection rules."""

    critical_score: float = 0.85
    warning_score: float = 0.60
    signal_quality_min: float = 0.30
    flatline_max_std: float = 0.001
    electrode_disconnect_threshold: float = 0.05
    seizure_spike_multiplier: float = 3.0
    expected_channels: int = 21


# ---------------------------------------------------------------------------
# Individual rules
# ---------------------------------------------------------------------------

def classify_anomaly(
    anomaly_score: float,
    signal_quality_score: float,
    thresholds: AnomalyThresholds | None = None,
) -> AlertDecision:
    """Classify anomaly based on score and signal quality.

    This is the primary classification rule.
    """
    t = thresholds or AnomalyThresholds()

    if signal_quality_score < t.signal_quality_min:
        return AlertDecision(
            severity="suppressed",
            explanation="Signal quality too low for a reliable alert.",
            triggered_rules=["signal_quality_gate"],
        )
    if anomaly_score >= t.critical_score:
        return AlertDecision(
            severity="critical",
            explanation="Critical anomaly score with acceptable signal quality.",
            triggered_rules=["critical_score"],
        )
    if anomaly_score >= t.warning_score:
        return AlertDecision(
            severity="warning",
            explanation="Elevated anomaly score requires review.",
            triggered_rules=["warning_score"],
        )
    return AlertDecision(
        severity="normal",
        explanation="No alert threshold was crossed.",
        triggered_rules=[],
    )


def detect_flatline(
    signal_std: float | None,
    thresholds: AnomalyThresholds | None = None,
) -> AlertDecision | None:
    """Detect flatline signal — may indicate electrode failure or brain death."""
    t = thresholds or AnomalyThresholds()

    if signal_std is None:
        return None
    if signal_std < t.flatline_max_std:
        return AlertDecision(
            severity="critical",
            explanation=f"Flatline detected: signal std={signal_std:.6f} below threshold={t.flatline_max_std}",
            triggered_rules=["flatline_detection"],
        )
    return None


def detect_electrode_disconnect(
    channel_count: int | None,
    thresholds: AnomalyThresholds | None = None,
) -> AlertDecision | None:
    """Detect excessive channel loss suggesting electrode disconnect."""
    t = thresholds or AnomalyThresholds()

    if channel_count is None:
        return None

    missing_ratio = 1.0 - (channel_count / t.expected_channels)
    if missing_ratio > t.electrode_disconnect_threshold:
        return AlertDecision(
            severity="warning",
            explanation=(
                f"Electrode disconnect suspected: {channel_count}/{t.expected_channels} channels "
                f"({missing_ratio:.1%} missing)"
            ),
            triggered_rules=["electrode_disconnect"],
        )
    return None


def detect_seizure_pattern(
    anomaly_score: float | None,
    signal_quality: float | None,
    thresholds: AnomalyThresholds | None = None,
) -> AlertDecision | None:
    """Heuristic seizure pattern detection.

    Flags events with high anomaly scores and adequate signal quality
    as potential seizure activity.
    """
    t = thresholds or AnomalyThresholds()

    if anomaly_score is None or signal_quality is None:
        return None
    if anomaly_score >= 0.8 and signal_quality >= 0.5:
        return AlertDecision(
            severity="critical",
            explanation=(
                f"Possible seizure pattern: anomaly_score={anomaly_score:.3f}, "
                f"signal_quality={signal_quality:.3f}"
            ),
            triggered_rules=["seizure_pattern"],
        )
    return None


# ---------------------------------------------------------------------------
# Rule chaining
# ---------------------------------------------------------------------------

def evaluate_all_rules(
    anomaly_score: float,
    signal_quality_score: float,
    signal_std: float | None = None,
    channel_count: int | None = None,
    thresholds: AnomalyThresholds | None = None,
) -> AlertDecision:
    """Run all anomaly rules and return the highest-severity decision.

    Rule priority (highest to lowest):
    1. Flatline detection
    2. Seizure pattern
    3. Score-based classification
    4. Electrode disconnect
    """
    t = thresholds or AnomalyThresholds()

    severity_rank = {"critical": 3, "warning": 2, "suppressed": 1, "normal": 0}
    best: AlertDecision = AlertDecision(severity="normal", explanation="No alert threshold was crossed.")
    all_triggered: list[str] = []

    # Run all rules
    candidates: list[AlertDecision | None] = [
        detect_flatline(signal_std, t),
        detect_seizure_pattern(anomaly_score, signal_quality_score, t),
        classify_anomaly(anomaly_score, signal_quality_score, t),
        detect_electrode_disconnect(channel_count, t),
    ]

    for decision in candidates:
        if decision is None:
            continue
        all_triggered.extend(decision.triggered_rules)
        if severity_rank.get(decision.severity, 0) > severity_rank.get(best.severity, 0):
            best = decision

    best.triggered_rules = all_triggered
    return best

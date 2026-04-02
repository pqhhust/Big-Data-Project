from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class AlertDecision:
    severity: str
    explanation: str


def classify_anomaly(anomaly_score: float, signal_quality_score: float) -> AlertDecision:
    if signal_quality_score < 0.3:
        return AlertDecision(
            severity="suppressed",
            explanation="Signal quality too low for a reliable alert.",
        )
    if anomaly_score >= 0.85:
        return AlertDecision(
            severity="critical",
            explanation="Critical anomaly score with acceptable signal quality.",
        )
    if anomaly_score >= 0.6:
        return AlertDecision(
            severity="warning",
            explanation="Elevated anomaly score requires review.",
        )
    return AlertDecision(
        severity="normal",
        explanation="No alert threshold was crossed.",
    )

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


# ---------------------------------------------------------------------------
# v2 — Kim-Quan
# ---------------------------------------------------------------------------

def compute_anomaly_score(features: dict) -> float:
    """Combine several windowed feature columns into a single 0–1 score."""
    chunk_term = min(features.get("eeg_chunk_count", 0) / 60.0, 1.0)
    quality_term = 1.0 - features.get("signal_quality_score", 1.0)
    critical_term = 0.6 if features.get("has_critical_lab") else 0.0
    meds_term = min(features.get("n_medication_changes_24h", 0) / 5.0, 1.0)

    score = (
        0.30 * chunk_term
        + 0.25 * quality_term
        + 0.30 * critical_term
        + 0.15 * meds_term
    )
    return max(0.0, min(score, 1.0))


def classify_v2(features: dict) -> AlertDecision:
    """4-tier severity ladder.

    Thresholds:
        signal_quality < 0.3                 → suppressed
        score >= 0.85                        → critical
        score >= 0.65                        → warning
        score >= 0.40                        → advisory
        else                                 → normal
    """

    signal_quality = features.get("signal_quality_score", 1.0)
    if signal_quality < 0.3:
        return AlertDecision(
            severity="suppressed",
            explanation="Signal quality is too low (< 0.3) for a reliable alert.",
        )

    score = compute_anomaly_score(features)
    if score >= 0.85:
        return AlertDecision(
            severity="critical",
            explanation=f"Critical anomaly score ({score:.2f}) observed.",
        )
    if score >= 0.65:
        return AlertDecision(
            severity="warning",
            explanation=f"Warning anomaly score ({score:.2f}) observed.",
        )
    if score >= 0.40:
        return AlertDecision(
            severity="advisory",
            explanation=f"Elevated anomaly score ({score:.2f}) observed.",
        )

    return AlertDecision(
        severity="normal",
        explanation="Metrics are within normal thresholds.",
    )

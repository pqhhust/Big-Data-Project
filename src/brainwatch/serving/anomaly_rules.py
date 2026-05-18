"""Anomaly classification rules.

v1: ``classify_anomaly`` (already shipped — keep it; the speed layer's UDF
still imports it).

v2: Kim-Quan extends this module with:
    - ``compute_anomaly_score`` — combines several feature columns into a
      0–1 score so the speed layer can call it from a UDF.
    - ``classify_v2`` — 4-tier severity ladder.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class AlertDecision:
    severity: str
    explanation: str


# ---------------------------------------------------------------------------
# v1 — already in production, do not break
# ---------------------------------------------------------------------------

def classify_anomaly(anomaly_score: float, signal_quality_score: float) -> AlertDecision:
    """Production v1 classifier — do not change signature."""
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
    """Combine several windowed feature columns into a single 0–1 score.

    Formula:
        chunk_term     = min(eeg_chunk_count / 60.0, 1.0)
        quality_term   = 1.0 - signal_quality_score
        critical_term  = 0.6 if has_critical_lab else 0.0
        meds_term      = min(n_medication_changes_24h / 5.0, 1.0)

        score = 0.30 * chunk_term + 0.25 * quality_term
              + 0.30 * critical_term + 0.15 * meds_term

    Returns a value clamped to [0.0, 1.0].
    """
    chunk_term = min(features.get("eeg_chunk_count", 0) / 60.0, 1.0)
    quality_term = 1.0 - features.get("signal_quality_score", 1.0)
    critical_term = 0.6 if features.get("has_critical_lab") else 0.0
    meds_term = min(features.get("n_medication_changes_24h", 0) / 5.0, 1.0)

    score = 0.30 * chunk_term + 0.25 * quality_term + 0.30 * critical_term + 0.15 * meds_term
    return max(0.0, min(score, 1.0))


def classify_v2(score: float, has_critical_lab: bool = False) -> AlertDecision:
    """4-tier severity ladder.

    Thresholds:
        score >= 0.85                        -> critical
        score >= 0.65                        -> warning
        score >= 0.40                        -> advisory
        else                                 -> normal
    """
    if has_critical_lab and score >= 0.6:
        return AlertDecision(
            severity="critical",
            explanation=f"Critical anomaly (score={score:.2f}) with critical lab value.",
        )
    if score >= 0.85:
        return AlertDecision(
            severity="critical",
            explanation=f"Critical anomaly score={score:.2f}.",
        )
    if score >= 0.65:
        return AlertDecision(
            severity="warning",
            explanation=f"Warning-level anomaly score={score:.2f}.",
        )
    if score >= 0.40:
        return AlertDecision(
            severity="advisory",
            explanation=f"Advisory-level anomaly score={score:.2f}.",
        )
    return AlertDecision(
        severity="normal",
        explanation=f"No significant anomaly detected (score={score:.2f}).",
    )
"""PySpark User-Defined Functions for EEG signal processing and feature extraction.

All UDFs are defined as plain Python functions first, then wrapped with
``F.udf()`` at the bottom of the module.  This allows unit testing without
a Spark context.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any


# ---------------------------------------------------------------------------
# Pure Python functions (testable without Spark)
# ---------------------------------------------------------------------------

def signal_quality_score(
    channel_count: int | None,
    sampling_rate_hz: float | None,
    window_seconds: float | None,
) -> float:
    """Compute a 0-1 signal quality score from basic EEG metadata.

    Heuristic based on:
    - Channel count (21 channels = 1.0, fewer = proportionally lower)
    - Sampling rate (256 Hz = 1.0, lower = proportionally lower)
    - Window completeness (1.0s = full, shorter = lower)
    """
    if channel_count is None or sampling_rate_hz is None or window_seconds is None:
        return 0.0

    cc_score = min(1.0, max(0.0, channel_count / 21.0))
    sr_score = min(1.0, max(0.0, sampling_rate_hz / 256.0))
    ws_score = min(1.0, max(0.0, window_seconds / 1.0))

    return round(0.4 * cc_score + 0.4 * sr_score + 0.2 * ws_score, 4)


def anomaly_severity(score: float | None) -> str:
    """Classify anomaly score into severity level."""
    if score is None:
        return "unknown"
    if score >= 0.85:
        return "critical"
    if score >= 0.60:
        return "warning"
    if score >= 0.30:
        return "elevated"
    return "normal"


def eeg_band_power(
    sampling_rate_hz: float | None,
    channel_count: int | None,
) -> dict[str, float]:
    """Estimate EEG frequency band power ratios from metadata.

    In a real system this would compute FFT on raw signal data.  Here
    we use a deterministic heuristic based on sampling rate for
    demonstration purposes.
    """
    if sampling_rate_hz is None or channel_count is None:
        return {"delta": 0.0, "theta": 0.0, "alpha": 0.0, "beta": 0.0, "gamma": 0.0}

    # Deterministic pseudo-ratios based on sampling rate
    base = sampling_rate_hz / 256.0
    return {
        "delta": round(0.35 * base, 4),
        "theta": round(0.25 * base, 4),
        "alpha": round(0.20 * base, 4),
        "beta": round(0.12 * base, 4),
        "gamma": round(0.08 * base, 4),
    }


def icd10_category(code: str | None) -> str:
    """Extract the ICD-10 category from a full code.

    Examples:
    - ``G40.0`` → ``G40``
    - ``I63.9`` → ``I63``
    - ``R56.9`` → ``R56``
    """
    if not code:
        return "UNKNOWN"
    return code.split(".")[0]


def detect_flatline(std_dev: float | None, threshold: float = 0.001) -> bool:
    """Return True if the signal standard deviation is below the flatline threshold."""
    if std_dev is None:
        return False
    return std_dev < threshold


def detect_electrode_disconnect(
    channel_count: int | None,
    expected_channels: int = 21,
    threshold: float = 0.05,
) -> bool:
    """Return True if too many channels are missing."""
    if channel_count is None or expected_channels <= 0:
        return False
    missing_ratio = 1.0 - (channel_count / expected_channels)
    return missing_ratio > threshold


def detect_seizure_pattern(
    anomaly_score: float | None,
    signal_quality: float | None,
    spike_multiplier: float = 3.0,
) -> bool:
    """Heuristic seizure pattern detection.

    Flags events with high anomaly scores and good signal quality.
    """
    if anomaly_score is None or signal_quality is None:
        return False
    return anomaly_score >= 0.8 and signal_quality >= 0.5


def compute_row_fingerprint(
    patient_id: str | None,
    session_id: str | None,
    event_time: str | None,
) -> str:
    """Compute a SHA-256 fingerprint for deduplication."""
    canonical = json.dumps(
        {"patient_id": patient_id or "", "session_id": session_id or "", "event_time": event_time or ""},
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# PySpark UDF registration (lazy import)
# ---------------------------------------------------------------------------

def register_udfs(spark: Any) -> dict[str, Any]:
    """Register all BrainWatch UDFs with the given SparkSession.

    Returns a dictionary of UDF references keyed by name.
    """
    from pyspark.sql import functions as F
    from pyspark.sql.types import (
        BooleanType,
        DoubleType,
        MapType,
        StringType,
    )

    udfs = {}

    udfs["signal_quality_score"] = F.udf(signal_quality_score, DoubleType())
    spark.udf.register("signal_quality_score", signal_quality_score, DoubleType())

    udfs["anomaly_severity"] = F.udf(anomaly_severity, StringType())
    spark.udf.register("anomaly_severity", anomaly_severity, StringType())

    udfs["eeg_band_power"] = F.udf(eeg_band_power, MapType(StringType(), DoubleType()))
    spark.udf.register("eeg_band_power", eeg_band_power, MapType(StringType(), DoubleType()))

    udfs["icd10_category"] = F.udf(icd10_category, StringType())
    spark.udf.register("icd10_category", icd10_category, StringType())

    udfs["detect_flatline"] = F.udf(detect_flatline, BooleanType())
    spark.udf.register("detect_flatline", detect_flatline, BooleanType())

    udfs["detect_electrode_disconnect"] = F.udf(detect_electrode_disconnect, BooleanType())
    spark.udf.register("detect_electrode_disconnect", detect_electrode_disconnect, BooleanType())

    udfs["detect_seizure_pattern"] = F.udf(detect_seizure_pattern, BooleanType())
    spark.udf.register("detect_seizure_pattern", detect_seizure_pattern, BooleanType())

    udfs["compute_row_fingerprint"] = F.udf(compute_row_fingerprint, StringType())
    spark.udf.register("compute_row_fingerprint", compute_row_fingerprint, StringType())

    return udfs

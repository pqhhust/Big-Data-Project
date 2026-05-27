"""Field-level validation rules and fingerprint computation for BrainWatch events."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from brainwatch.contracts.events import (
    EEG_REQUIRED_FIELDS,
    EHR_REQUIRED_FIELDS,
    ALERT_REQUIRED_FIELDS,
    compute_fingerprint,
    validate_required_fields,
)


# ---------------------------------------------------------------------------
# Timestamp validation
# ---------------------------------------------------------------------------

_ISO_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
)


def is_valid_timestamp(value: str) -> bool:
    """Check whether *value* looks like a valid ISO-8601 timestamp."""
    if not value or not isinstance(value, str):
        return False
    if not _ISO_PATTERN.match(value):
        return False
    try:
        # Accept common ISO formats
        cleaned = value.replace("Z", "+00:00")
        datetime.fromisoformat(cleaned)
        return True
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# Numeric validation
# ---------------------------------------------------------------------------

def is_positive_int(value: Any) -> bool:
    """Return True if *value* is a positive integer."""
    try:
        return int(value) > 0
    except (TypeError, ValueError):
        return False


def is_non_negative_float(value: Any) -> bool:
    """Return True if *value* is a non-negative float."""
    try:
        return float(value) >= 0.0
    except (TypeError, ValueError):
        return False


def is_valid_score(value: Any, min_val: float = 0.0, max_val: float = 1.0) -> bool:
    """Return True if *value* is a float within [min_val, max_val]."""
    try:
        v = float(value)
        return min_val <= v <= max_val
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Domain validation
# ---------------------------------------------------------------------------

_VALID_SEVERITIES = {"normal", "warning", "critical", "suppressed"}
_VALID_EHR_EVENT_TYPES = {
    "admission", "discharge", "lab_result", "critical_lab",
    "vital_signs", "medication", "diagnosis", "procedure",
    "imaging", "consultation", "note",
}


def is_valid_severity(severity: str) -> bool:
    """Return True if *severity* is a recognized alert level."""
    return severity in _VALID_SEVERITIES


def is_valid_ehr_event_type(event_type: str) -> bool:
    """Return True if *event_type* is a recognized EHR event category."""
    return event_type in _VALID_EHR_EVENT_TYPES


# ---------------------------------------------------------------------------
# Composite validators
# ---------------------------------------------------------------------------

def validate_eeg_event(payload: dict[str, Any]) -> list[str]:
    """Validate an EEG event and return a list of error messages.

    Returns an empty list if the event is valid.
    """
    errors: list[str] = []

    # Required fields
    missing = validate_required_fields(payload, EEG_REQUIRED_FIELDS)
    if missing:
        errors.append(f"Missing required fields: {', '.join(missing)}")

    # Timestamp
    event_time = payload.get("event_time", "")
    if event_time and not is_valid_timestamp(str(event_time)):
        errors.append(f"Invalid event_time format: {event_time}")

    # Channel count
    cc = payload.get("channel_count")
    if cc is not None and not is_positive_int(cc):
        errors.append(f"channel_count must be a positive integer, got: {cc}")

    # Sampling rate
    sr = payload.get("sampling_rate_hz")
    if sr is not None and not is_non_negative_float(sr):
        errors.append(f"sampling_rate_hz must be non-negative, got: {sr}")

    return errors


def validate_ehr_event(payload: dict[str, Any]) -> list[str]:
    """Validate an EHR event and return a list of error messages."""
    errors: list[str] = []

    missing = validate_required_fields(payload, EHR_REQUIRED_FIELDS)
    if missing:
        errors.append(f"Missing required fields: {', '.join(missing)}")

    event_time = payload.get("event_time", "")
    if event_time and not is_valid_timestamp(str(event_time)):
        errors.append(f"Invalid event_time format: {event_time}")

    event_type = payload.get("event_type", "")
    if event_type and not is_valid_ehr_event_type(event_type):
        errors.append(f"Unknown event_type: {event_type}")

    version = payload.get("version")
    if version is not None and not is_positive_int(version):
        errors.append(f"version must be a positive integer, got: {version}")

    return errors


def validate_alert_event(payload: dict[str, Any]) -> list[str]:
    """Validate an alert event and return a list of error messages."""
    errors: list[str] = []

    missing = validate_required_fields(payload, ALERT_REQUIRED_FIELDS)
    if missing:
        errors.append(f"Missing required fields: {', '.join(missing)}")

    severity = payload.get("severity", "")
    if severity and not is_valid_severity(severity):
        errors.append(f"Invalid severity: {severity}")

    score = payload.get("anomaly_score")
    if score is not None and not is_valid_score(score, 0.0, 1.0):
        errors.append(f"anomaly_score must be in [0, 1], got: {score}")

    return errors


def compute_dedup_key(payload: dict[str, Any]) -> str:
    """Compute a deduplication key for an event payload.

    This is a convenience wrapper around ``compute_fingerprint`` that
    strips volatile metadata fields before hashing.
    """
    return compute_fingerprint(payload)

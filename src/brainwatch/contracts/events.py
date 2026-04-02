from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class EEGChunkEvent:
    patient_id: str
    session_id: str
    event_time: str
    site_id: str
    channel_count: int
    sampling_rate_hz: float
    window_seconds: float
    source_uri: str


@dataclass(slots=True)
class EHREvent:
    patient_id: str
    encounter_id: str
    event_time: str
    event_type: str
    source_system: str
    version: int
    payload: dict[str, Any]


@dataclass(slots=True)
class FeatureEvent:
    patient_id: str
    session_id: str
    window_end: str
    anomaly_score: float
    signal_quality_score: float
    feature_values: dict[str, float]


@dataclass(slots=True)
class AlertEvent:
    patient_id: str
    session_id: str
    alert_time: str
    severity: str
    anomaly_score: float
    explanation: str


def to_payload(record: EEGChunkEvent | EHREvent | FeatureEvent | AlertEvent) -> dict[str, Any]:
    return asdict(record)


def validate_required_fields(payload: dict[str, Any], required_fields: set[str]) -> list[str]:
    return [field for field in sorted(required_fields) if payload.get(field) in (None, "")]

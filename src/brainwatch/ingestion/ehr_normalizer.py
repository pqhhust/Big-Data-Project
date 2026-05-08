"""Synthetic EHR generator + normaliser.

Owner: **Kim-Quan**.
Depends on: ``brainwatch.contracts.events.EHREvent``, Kim-Hung's
``kafka_helpers.get_producer``.

Why synthetic? The BDSP corpus only ships EEG signals; the matching EHR side
is private hospital data. For Week 2 we generate plausible synthetic EHR events
for each subject in the download manifest, so the join in the speed layer
(Quang-Hung's bronze_ingest) has something realistic to land on.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from brainwatch.contracts.events import EHREvent

EHR_EVENT_TYPES = ("vital_signs", "lab_result", "medication", "critical_lab", "note")


def generate_ehr_from_manifest(manifest_path: Path,
                               events_per_subject: int = 5) -> list[EHREvent]:
    """For each subject in the manifest, emit ``events_per_subject`` synthetic
    ``EHREvent`` instances spread across the 5 event types.

    Suggested distribution (you can tweak — just keep ``critical_lab`` rare):
      vital_signs 40%, lab_result 25%, medication 20%, note 10%, critical_lab 5%.

    Field generation tips:
      - ``encounter_id`` = ``f"enc_{patient_id}_{i:03d}"``
      - ``event_time`` = ISO-8601, jitter around ``datetime.utcnow()`` by ±30 min
      - ``source_system`` = ``"epic"`` or ``"cerner"`` (random)
      - ``version`` = 1
      - ``payload`` = small dict with type-appropriate fields:
          vital_signs   → {"hr": 72, "spo2": 0.98, "rr": 16}
          lab_result    → {"test": "Na", "value": 138, "unit": "mEq/L"}
          medication    → {"drug": "levetiracetam", "dose_mg": 500}
          critical_lab  → {"test": "lactate", "value": 4.5, "unit": "mmol/L"}
          note          → {"text": "Routine neuro check, no AED change"}

    Kim-Quan: implement.
    """
    # Kim-Quan: code synthetic EHR generation here.
    pass


def normalize_ehr_payload(raw: dict[str, Any]) -> EHREvent:
    """Coerce a free-form dict into a valid ``EHREvent``.

    Steps:
      1. lower-case ``event_type``; default to ``"note"`` if missing.
      2. ensure ``event_time`` is an ISO-8601 string (parse + reformat if it's
         already a ``datetime``).
      3. default ``version=1`` if missing.
      4. ``source_system`` default = ``"unknown"``.
      5. unknown keys (anything not in the dataclass) go into ``payload``.

    Raises ``ValueError`` if ``patient_id`` or ``encounter_id`` is missing —
    those are the two we genuinely cannot synthesise.

    Kim-Quan: implement.
    """
    # Kim-Quan: code normalisation here.
    pass


def publish_ehr_events(
    events: list[EHREvent],
    topic: str = "ehr.updates",
    bootstrap_servers: str = "localhost:9092",
    replay_speed: float = 0.0,
    fallback_path: str | None = None,
) -> dict[str, Any]:
    """Mirror of ``eeg_producer.publish_events`` for the ``ehr.updates`` topic.

    Kim-Quan: same shape — ``get_producer``, validate, send, flush. Return
    ``{"published": N, "failed": M, "validation_errors": K}``.
    """
    # Kim-Quan: code the publish loop here.
    pass

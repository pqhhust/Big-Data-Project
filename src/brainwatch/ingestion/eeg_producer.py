"""EEG metadata-to-event publisher.

Owner: **Kim-Quan**.
Depends on: Kim-Hung's ``kafka_helpers.get_producer``, and the
canonical ``EEGChunkEvent`` schema in ``brainwatch.contracts.events``.

Reads a download manifest produced by ``scripts/download_eeg_ehr.py`` (Trang)
and publishes ``EEGChunkEvent`` messages to the ``eeg.raw`` Kafka topic.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from brainwatch.contracts.events import EEGChunkEvent

EEG_REQUIRED = {"patient_id", "session_id", "event_time", "site_id"}


def manifest_to_events(manifest_path: Path) -> list[EEGChunkEvent]:
    """Read the JSON manifest and turn each record into an ``EEGChunkEvent``.

    Manifest record shape (see Trang's ``build_manifest`` in
    ``scripts/download_eeg_ehr.py``)::

        {"subject_id": ..., "session_id": ..., "site_id": ...,
         "duration_seconds": ..., "s3_keys": ["..."]}

    Field mapping:
      - ``patient_id``        ← ``subject_id``
      - ``session_id``        ← ``session_id``
      - ``event_time``        ← ``datetime.now(tz=timezone.utc).isoformat()``
                                (real arrival time; we don't have true timestamps)
      - ``site_id``           ← ``site_id``
      - ``channel_count``     ← 19 (matches BDSP 10-20 montage)
      - ``sampling_rate_hz``  ← 200.0 (matches the parent pretraining pipeline)
      - ``window_seconds``    ← ``duration_seconds``
      - ``source_uri``        ← ``s3_keys[0]``

    Kim-Quan: implement.
    """
    # Kim-Quan: code manifest → events here.
    pass


def publish_events(
    events: list[EEGChunkEvent],
    topic: str = "eeg.raw",
    bootstrap_servers: str = "localhost:9092",
    replay_speed: float = 0.0,
    fallback_path: str | None = None,
) -> dict[str, Any]:
    """Publish events to Kafka (or the file fallback). Returns stats dict
    ``{"published": N, "failed": M, "validation_errors": K}``.

    ``replay_speed``:
      - ``0`` → batch mode, no sleep between events.
      - ``N > 0`` → simulate Nx realtime by sleeping
        ``window_seconds / replay_speed`` between events.

    Kim-Quan: implement.
      1. ``producer = get_producer(bootstrap_servers, fallback_path)``
      2. for each event:
         - ``validate_required_fields(asdict(event), EEG_REQUIRED)``
           → bump ``validation_errors`` and ``continue`` if missing.
         - ``producer.send(topic, event)`` inside try/except → bump ``published``
           or ``failed``.
         - sleep if ``replay_speed > 0``.
      3. ``producer.flush(); producer.close()``
      4. return stats.
    """
    # Kim-Quan: code the publish loop here.
    pass

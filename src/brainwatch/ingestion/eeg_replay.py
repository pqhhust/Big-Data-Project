from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from brainwatch.config.settings import load_settings
from brainwatch.contracts.events import EEGChunkEvent, to_payload


@dataclass(frozen=True, slots=True)
class ReplayConfig:
    manifest_path: Path
    bootstrap_servers: str
    topic: str
    channel_count: int = 19
    sampling_rate_hz: float = 256.0
    window_seconds: float = 10.0
    replay_speed: float = 20.0
    max_events: int | None = None
    dry_run: bool = False
    start_time: datetime | None = None


def load_subset_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path)
    with manifest_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict) or "records" not in payload:
        raise ValueError("Invalid subset manifest: missing 'records'")
    if not isinstance(payload["records"], list):
        raise ValueError("Invalid subset manifest: 'records' must be a list")
    return payload


def iter_manifest_records(manifest: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for record in manifest.get("records", []):
        if isinstance(record, dict):
            yield record


def _isoformat_z(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def generate_eeg_chunk_events(
    manifest_record: dict[str, Any],
    *,
    channel_count: int,
    sampling_rate_hz: float,
    window_seconds: float,
    start_time: datetime,
) -> Iterator[EEGChunkEvent]:
    site_id = (manifest_record.get("site_id") or "UNKNOWN").strip() or "UNKNOWN"
    patient_id = (manifest_record.get("subject_id") or "").strip()
    session_id = str(manifest_record.get("session_id") or "").strip()
    duration_seconds = float(manifest_record.get("duration_seconds") or 0.0)
    keys = manifest_record.get("candidate_source_keys") or []
    source_uri = keys[0] if isinstance(keys, list) and keys else ""

    if not patient_id or not session_id:
        return

    if window_seconds <= 0:
        raise ValueError("window_seconds must be > 0")
    if duration_seconds <= 0:
        # Unknown duration; emit a single event so the pipeline can be tested.
        duration_seconds = window_seconds

    event_count = max(1, int(duration_seconds // window_seconds))
    for index in range(event_count):
        event_time = start_time + timedelta(seconds=index * window_seconds)
        yield EEGChunkEvent(
            patient_id=patient_id,
            session_id=session_id,
            event_time=_isoformat_z(event_time),
            site_id=site_id,
            channel_count=channel_count,
            sampling_rate_hz=sampling_rate_hz,
            window_seconds=window_seconds,
            source_uri=source_uri,
        )


def _resolve_kafka_settings(*, settings_path: str | None, bootstrap_servers: str | None, topic: str | None) -> tuple[str, str]:
    if bootstrap_servers and topic:
        return bootstrap_servers, topic
    if settings_path:
        settings = load_settings(settings_path)
        kafka = settings.raw.get("kafka", {})
        resolved_bootstrap = bootstrap_servers or kafka.get("bootstrap_servers")
        resolved_topic = topic or kafka.get("topics", {}).get("eeg_raw")
        if resolved_bootstrap and resolved_topic:
            return str(resolved_bootstrap), str(resolved_topic)
    raise ValueError("Missing Kafka config: provide --bootstrap-servers and --topic, or --settings with kafka.*")


def publish_events(
    events: Iterable[EEGChunkEvent],
    *,
    bootstrap_servers: str,
    topic: str,
    replay_speed: float,
    max_events: int | None,
    dry_run: bool,
) -> int:
    if replay_speed <= 0:
        raise ValueError("replay_speed must be > 0")

    if dry_run:
        sent = 0
        for event in events:
            print(json.dumps(to_payload(event), ensure_ascii=False))
            sent += 1
            if max_events is not None and sent >= max_events:
                break
        return sent

    try:
        from kafka import KafkaProducer
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "Kafka producer dependency is missing. Install with: pip install -e '.[kafka]'"
        ) from exc

    producer = KafkaProducer(
        bootstrap_servers=bootstrap_servers,
        acks="all",
        retries=10,
        linger_ms=50,
        key_serializer=lambda b: b,
        value_serializer=lambda payload: json.dumps(payload).encode("utf-8"),
    )

    sent = 0
    last_emit = time.monotonic()
    for event in events:
        payload = to_payload(event)
        key = f"{event.patient_id}:{event.session_id}".encode("utf-8")
        producer.send(topic, key=key, value=payload)
        sent += 1

        if max_events is not None and sent >= max_events:
            break

        # Approximate real-time pacing based on window size.
        target_interval = float(event.window_seconds) / replay_speed
        now = time.monotonic()
        elapsed = now - last_emit
        if target_interval > 0 and elapsed < target_interval:
            time.sleep(target_interval - elapsed)
        last_emit = time.monotonic()

    producer.flush(timeout=30)
    producer.close(timeout=30)
    return sent


def replay_manifest_to_kafka(config: ReplayConfig, *, settings_path: str | None = None) -> int:
    manifest = load_subset_manifest(config.manifest_path)

    bootstrap, topic = _resolve_kafka_settings(
        settings_path=settings_path,
        bootstrap_servers=config.bootstrap_servers,
        topic=config.topic,
    )

    base_time = config.start_time or datetime.now(timezone.utc)

    def iter_all_events() -> Iterator[EEGChunkEvent]:
        current = base_time
        for record in iter_manifest_records(manifest):
            yield from generate_eeg_chunk_events(
                record,
                channel_count=config.channel_count,
                sampling_rate_hz=config.sampling_rate_hz,
                window_seconds=config.window_seconds,
                start_time=current,
            )
            duration = float(record.get("duration_seconds") or config.window_seconds)
            current = current + timedelta(seconds=max(config.window_seconds, duration))

    return publish_events(
        iter_all_events(),
        bootstrap_servers=bootstrap,
        topic=topic,
        replay_speed=config.replay_speed,
        max_events=config.max_events,
        dry_run=config.dry_run,
    )

"""EDF-to-Kafka publisher.

Owner: **Kim-Hung**.
Reads real EDF files using ``mne`` and publishes ``EEGChunkEvent`` messages
to the ``eeg.raw`` Kafka topic. This complements the manifest-based publisher.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from brainwatch.contracts.events import EEGChunkEvent
from brainwatch.ingestion.kafka_helpers import get_producer

def publish_edf_file(
    edf_path: str,
    patient_id: str,
    session_id: str,
    site_id: str,
    chunk_duration_seconds: float = 10.0,
    topic: str = "eeg.raw",
    bootstrap_servers: str = "localhost:9092",
    replay_speed: float = 0.0,
) -> dict[str, Any]:
    """Read an EDF file, split into chunks, and publish to Kafka."""
    import mne
    
    producer = get_producer(
        bootstrap_servers=bootstrap_servers,
        max_in_flight_requests_per_connection=1
    )
    
    stats = {"published": 0, "failed": 0, "validation_errors": 0}
    
    try:
        # Load EDF without preloading data to save memory
        raw = mne.io.read_raw_edf(edf_path, preload=False, verbose=False)
        sfreq = raw.info['sfreq']
        n_channels = len(raw.ch_names)
        total_duration = raw.times[-1] if len(raw.times) > 0 else 0
        
        current_time = 0.0
        while current_time < total_duration:
            duration = min(chunk_duration_seconds, total_duration - current_time)
            if duration <= 0:
                break
                
            event = EEGChunkEvent(
                patient_id=patient_id,
                session_id=session_id,
                event_time=datetime.now(tz=timezone.utc).isoformat(),
                site_id=site_id,
                channel_count=n_channels,
                sampling_rate_hz=float(sfreq),
                window_seconds=float(duration),
                source_uri=f"file://{os.path.abspath(edf_path)}?start={current_time}&duration={duration}"
            )
            
            try:
                producer.send(topic, event)
                stats["published"] += 1
            except Exception:
                stats["failed"] += 1
                
            if replay_speed > 0:
                time.sleep(duration / replay_speed)
                
            current_time += duration
            
    except Exception as e:
        print(f"Error reading EDF file: {e}")
        stats["failed"] += 1
    finally:
        producer.flush()
        producer.close()
        
    return stats

if __name__ == "__main__":
    # Example usage for manual execution
    import sys
    if len(sys.argv) > 1:
        edf_file = sys.argv[1]
        print(f"Publishing {edf_file} to Kafka...")
        publish_edf_file(edf_file, patient_id="p_test", session_id="s_test", site_id="site_test")

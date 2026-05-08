"""Tests for ``brainwatch.ingestion.eeg_producer``.

Owner: **Trang**.  Covers Kim-Quan's manifest → events → publish path.
"""
from __future__ import annotations

import json
from pathlib import Path


def _write_manifest(tmp_path: Path, n_records: int = 3) -> Path:
    """Trang: write a tiny manifest JSON with ``n_records`` records, return
    the path. Use the field shape produced by Trang's ``build_manifest``:
    subject_id, session_id, site_id, duration_seconds, s3_keys."""
    # Trang: code the manifest fixture here.
    pass


def test_manifest_to_events_maps_fields(tmp_path: Path):
    """Trang: write a 1-record manifest, call manifest_to_events, assert the
    EEGChunkEvent has the expected patient_id/site_id/window_seconds and the
    defaults (channel_count=19, sampling_rate_hz=200.0)."""
    pass


def test_publish_events_uses_file_fallback(tmp_path: Path):
    """Trang: monkey-patch get_producer to return a FileProducer pointing at
    tmp_path, publish 3 events, assert 3 lines written and stats[``published``] == 3."""
    pass


def test_publish_events_counts_validation_errors(tmp_path: Path):
    """Trang: build one event with empty session_id; publish; assert
    stats[``validation_errors``] == 1 and stats[``published``] == 0."""
    pass

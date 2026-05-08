"""Tests for ``brainwatch.ingestion.bronze_writer``.

Owner: **Trang**.  Covers Kim-Hung's ``BronzeWriter``.
"""
from __future__ import annotations

from pathlib import Path

# Trang: uncomment once Kim-Hung lands the module:
#   from brainwatch.ingestion.bronze_writer import BronzeWriter
#   from brainwatch.contracts.events import EEGChunkEvent, EHREvent


def _make_eeg_event(**overrides):
    """Trang: small helper that returns a valid EEGChunkEvent with sane defaults
    so each test can override one field at a time."""
    # Trang: code the helper here.
    pass


def test_writes_eeg_event_to_partitioned_path(tmp_path: Path):
    """Trang: write one valid EEG event, assert
    ``<bronze>/eeg/site=<site>/date=YYYY-MM-DD/eeg_bronze_*.jsonl`` exists."""
    pass


def test_dedup_ignores_duplicate_events(tmp_path: Path):
    """Trang: write the same event twice. Stats should be
    ``{written: 1, duplicates: 1, errors: 0}``."""
    pass


def test_invalid_event_routed_to_dlq(tmp_path: Path):
    """Trang: write a dict that's missing ``site_id`` via write_raw, assert
    stats has ``errors: 1`` and the DLQ file contains one record with
    ``reason`` mentioning the missing field."""
    pass


def test_ehr_event_partitions_by_date_only(tmp_path: Path):
    """Trang: EHR has no ``site_id`` partition. Assert path is
    ``<bronze>/ehr/date=YYYY-MM-DD/ehr_bronze_*.jsonl``."""
    pass

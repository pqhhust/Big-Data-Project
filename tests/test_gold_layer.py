"""Tests for ``brainwatch.processing.gold_layer``.

Owner: **Trang**.  Code under test: Kim-Hung.
"""
from __future__ import annotations

import importlib

import pytest


def test_module_importable_without_pyspark():
    pass


@pytest.mark.skipif(
    importlib.util.find_spec("pyspark") is None,
    reason="pyspark not installed",
)
def test_patient_features_aggregates_per_day(tmp_path):
    """Trang: write a silver fixture with 3 EEG rows on 2026-05-09 and
    2 EEG rows on 2026-05-10 for the same patient. Run
    build_patient_features. Assert the gold parquet has exactly two rows
    for that patient with n_eeg_chunks of 3 and 2 respectively."""
    pass


@pytest.mark.skipif(
    importlib.util.find_spec("pyspark") is None,
    reason="pyspark not installed",
)
def test_critical_lab_flag_propagates(tmp_path):
    """Trang: silver EHR fixture with one event_type='critical_lab' inside
    the ±30 min window of an EEG row → has_critical_lab_today should be 1."""
    pass


@pytest.mark.skipif(
    importlib.util.find_spec("pyspark") is None,
    reason="pyspark not installed",
)
def test_patient_dim_join_uses_broadcast(tmp_path, capsys):
    """Trang: harder one — call ``df.explain()`` and assert
    ``BroadcastHashJoin`` appears in the captured stdout. This is the
    optimization Kim-Hung is supposed to enforce."""
    pass

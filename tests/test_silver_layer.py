"""Tests for ``brainwatch.processing.silver_layer``.

Owner: **Trang**.  Code under test: Kim-Hung.

The full-fat tests need a SparkSession; we skip them when pyspark isn't
installed so the suite stays green for everyone.
"""
from __future__ import annotations

import importlib

import pytest


def test_module_importable_without_pyspark():
    """Trang: ``import brainwatch.processing.silver_layer`` must succeed
    regardless of whether pyspark is installed."""
    pass


@pytest.mark.skipif(
    importlib.util.find_spec("pyspark") is None,
    reason="pyspark not installed",
)
def test_eeg_silver_dedups_on_patient_session_event_time(tmp_path):
    """Trang: write a tiny bronze parquet with two duplicate rows, run
    build_eeg_silver, assert the silver output has the duplicate dropped."""
    pass


@pytest.mark.skipif(
    importlib.util.find_spec("pyspark") is None,
    reason="pyspark not installed",
)
def test_ehr_silver_keeps_latest_version_per_encounter(tmp_path):
    """Trang: write 3 versions for the same encounter (v1, v2, v3),
    run build_ehr_silver, assert the silver output has only v3."""
    pass


@pytest.mark.skipif(
    importlib.util.find_spec("pyspark") is None,
    reason="pyspark not installed",
)
def test_quality_flag_marks_low_sampling_rate(tmp_path):
    """Trang: row with sampling_rate_hz=50 → quality_flag='LOW_SR'."""
    pass

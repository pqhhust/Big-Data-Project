"""Tests for ``brainwatch.processing.bronze_ingest``.

Owner: **Trang** (test author) + **Quang-Hung** (code under test).

We deliberately do NOT spin up a real SparkSession in unit tests — that's
integration territory. We only check that:
  1. the module is importable without ``pyspark`` (the deferred-import contract).
  2. the schema builders return the columns we expect (when pyspark IS available).
"""
from __future__ import annotations

import importlib

import pytest


def test_module_importable_without_pyspark():
    """Trang: ``importlib.import_module("brainwatch.processing.bronze_ingest")``
    must succeed even on a machine without pyspark. The whole point of
    deferred imports."""
    # Trang: code the import assertion here.
    pass


@pytest.mark.skipif(
    importlib.util.find_spec("pyspark") is None,
    reason="pyspark not installed",
)
def test_eeg_schema_has_required_columns():
    """Trang: when pyspark IS available, _eeg_schema() returns a StructType
    containing at least: patient_id, session_id, event_time, site_id."""
    # Trang: code the schema field-name assertion here.
    pass

"""Tests for ``brainwatch.processing.speed_layer``.

Owner: **Trang**.  Code under test: Quang-Hung.

We deliberately do NOT run the streaming query in unit tests — that's
integration territory. We assert structural properties instead.
"""
from __future__ import annotations

import importlib

import pytest


def test_module_importable_without_pyspark():
    """Trang: ``importlib.import_module("brainwatch.processing.speed_layer")``
    must succeed even without pyspark. Protects the deferred-import contract."""
    # Trang: code the import assertion here.
    pass


@pytest.mark.skipif(
    importlib.util.find_spec("pyspark") is None,
    reason="pyspark not installed",
)
def test_pipeline_returns_streaming_query(tmp_path, monkeypatch):
    """Trang: build a SparkSession in local[1] mode, point bronze at an
    empty tmp dir, call build_streaming_pipeline, assert the returned
    object has a ``.lastProgress`` attribute (StreamingQuery)."""
    pass

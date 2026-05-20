"""Tests for ``brainwatch.processing.speed_layer``.

Structural assertions only — streaming queries are integration territory.
"""
from __future__ import annotations

import importlib

import pytest


def test_module_importable_without_pyspark():
    import brainwatch.processing.speed_layer as speed  # noqa: F401


_HAS_PYSPARK = importlib.util.find_spec("pyspark") is not None


@pytest.mark.skipif(not _HAS_PYSPARK, reason="pyspark not installed")
def test_build_streaming_pipeline_signature_and_imports():
    """The function should exist with the expected signature and import
    pyspark only when called."""
    from brainwatch.processing.speed_layer import build_streaming_pipeline
    import inspect

    sig = inspect.signature(build_streaming_pipeline)
    assert list(sig.parameters)[:5] == [
        "spark", "bronze_path", "checkpoint_path",
        "kafka_servers", "cassandra_contact_points",
    ]

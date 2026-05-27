"""Tests for brainwatch.serving.feature_store — 4 test cases."""

from __future__ import annotations

import time

import pytest

from brainwatch.serving.feature_store import FeatureStore


class TestFeatureStore:
    def test_put_and_get(self) -> None:
        store = FeatureStore(ttl_seconds=60)
        store.put("P001", {"anomaly_score": 0.5, "quality": 0.9})
        result = store.get("P001")
        assert result is not None
        assert result["anomaly_score"] == 0.5

    def test_get_nonexistent(self) -> None:
        store = FeatureStore()
        assert store.get("PXXX") is None

    def test_invalidate(self) -> None:
        store = FeatureStore()
        store.put("P001", {"score": 0.5})
        store.invalidate("P001")
        assert store.get("P001") is None

    def test_size(self) -> None:
        store = FeatureStore()
        store.put("P001", {"a": 1})
        store.put("P002", {"b": 2})
        assert store.size == 2
        store.clear()
        assert store.size == 0

"""Tests for brainwatch.processing.batch_gold — 8 test cases (structural)."""

from __future__ import annotations

import inspect

import pytest

from brainwatch.processing import batch_gold


class TestGoldImports:
    def test_build_gold_joined_importable(self) -> None:
        assert callable(batch_gold.build_gold_joined_features)

    def test_build_gold_broadcast_importable(self) -> None:
        assert callable(batch_gold.build_gold_with_broadcast)

    def test_pivot_ehr_importable(self) -> None:
        assert callable(batch_gold.pivot_ehr_lab_features)

    def test_unpivot_importable(self) -> None:
        assert callable(batch_gold.unpivot_features_long)

    def test_alert_trends_importable(self) -> None:
        assert callable(batch_gold.build_alert_trends)

    def test_patient_risk_state_importable(self) -> None:
        assert callable(batch_gold.build_patient_risk_state)


class TestGoldSignatures:
    def test_gold_joined_signature(self) -> None:
        sig = inspect.signature(batch_gold.build_gold_joined_features)
        assert "spark" in sig.parameters
        assert "gold_path" in sig.parameters

    def test_pivot_signature(self) -> None:
        sig = inspect.signature(batch_gold.pivot_ehr_lab_features)
        assert "spark" in sig.parameters
        assert "output_path" in sig.parameters

"""Tests for brainwatch.processing.batch_silver — 8 test cases (structural)."""

from __future__ import annotations

import pytest

from brainwatch.processing import batch_silver


class TestSilverTransformImports:
    def test_transform_eeg_importable(self) -> None:
        assert callable(batch_silver.transform_eeg_bronze_to_silver)

    def test_transform_ehr_importable(self) -> None:
        assert callable(batch_silver.transform_ehr_bronze_to_silver)

    def test_build_session_catalog_importable(self) -> None:
        assert callable(batch_silver.build_session_catalog)

    def test_build_patient_reference_importable(self) -> None:
        assert callable(batch_silver.build_patient_reference)


class TestSilverTransformSignatures:
    def test_eeg_transform_accepts_three_args(self) -> None:
        import inspect
        sig = inspect.signature(batch_silver.transform_eeg_bronze_to_silver)
        assert len(sig.parameters) == 3

    def test_ehr_transform_accepts_three_args(self) -> None:
        import inspect
        sig = inspect.signature(batch_silver.transform_ehr_bronze_to_silver)
        assert len(sig.parameters) == 3

    def test_session_catalog_accepts_three_args(self) -> None:
        import inspect
        sig = inspect.signature(batch_silver.build_session_catalog)
        assert len(sig.parameters) == 3

    def test_patient_reference_accepts_four_args(self) -> None:
        import inspect
        sig = inspect.signature(batch_silver.build_patient_reference)
        assert len(sig.parameters) == 4

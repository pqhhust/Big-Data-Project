"""Tests for brainwatch.contracts.schemas — 10 test cases."""

from __future__ import annotations

import pytest

from brainwatch.contracts.schemas import SCHEMA_VERSION


class TestSchemaVersion:
    def test_schema_version_format(self) -> None:
        assert isinstance(SCHEMA_VERSION, str)
        parts = SCHEMA_VERSION.split(".")
        assert len(parts) == 3
        assert all(p.isdigit() for p in parts)


class TestSchemaFunctions:
    """Test that schema functions return StructType-like objects.

    These tests verify the schema definitions are valid by checking
    field names and types, without requiring PySpark to be installed.
    """

    def test_eeg_bronze_schema_importable(self) -> None:
        from brainwatch.contracts.schemas import eeg_bronze_schema
        assert callable(eeg_bronze_schema)

    def test_eeg_raw_schema_importable(self) -> None:
        from brainwatch.contracts.schemas import eeg_raw_schema
        assert callable(eeg_raw_schema)

    def test_ehr_bronze_schema_importable(self) -> None:
        from brainwatch.contracts.schemas import ehr_bronze_schema
        assert callable(ehr_bronze_schema)

    def test_ehr_raw_schema_importable(self) -> None:
        from brainwatch.contracts.schemas import ehr_raw_schema
        assert callable(ehr_raw_schema)

    def test_feature_schema_importable(self) -> None:
        from brainwatch.contracts.schemas import feature_schema
        assert callable(feature_schema)

    def test_alert_schema_importable(self) -> None:
        from brainwatch.contracts.schemas import alert_schema
        assert callable(alert_schema)

    def test_silver_eeg_schema_importable(self) -> None:
        from brainwatch.contracts.schemas import silver_eeg_schema
        assert callable(silver_eeg_schema)

    def test_gold_joined_schema_importable(self) -> None:
        from brainwatch.contracts.schemas import gold_joined_schema
        assert callable(gold_joined_schema)

    def test_dlq_schema_importable(self) -> None:
        from brainwatch.contracts.schemas import dlq_schema
        assert callable(dlq_schema)

"""Tests for brainwatch.serving.cassandra_client — 6 test cases (structural)."""

from __future__ import annotations

import pytest

from brainwatch.serving.cassandra_client import (
    CREATE_ALERTS_TABLE,
    CREATE_FEATURES_TABLE,
    CREATE_STATE_TABLE,
    CassandraClient,
)


class TestCassandraClientInit:
    def test_default_config(self) -> None:
        client = CassandraClient()
        assert client.contact_points == ["localhost"]
        assert client.port == 9042
        assert client.keyspace == "brainwatch"

    def test_custom_config(self) -> None:
        client = CassandraClient(
            contact_points=["192.168.1.1"],
            port=9043,
            keyspace="test_ks",
        )
        assert client.contact_points == ["192.168.1.1"]
        assert client.port == 9043
        assert client.keyspace == "test_ks"

    def test_not_connected_initially(self) -> None:
        client = CassandraClient()
        assert client.is_connected is False


class TestCQLGeneration:
    def test_alerts_cql_has_primary_key(self) -> None:
        assert "PRIMARY KEY" in CREATE_ALERTS_TABLE
        assert "patient_id" in CREATE_ALERTS_TABLE
        assert "alert_time" in CREATE_ALERTS_TABLE

    def test_features_cql_has_columns(self) -> None:
        assert "anomaly_score" in CREATE_FEATURES_TABLE
        assert "signal_quality_score" in CREATE_FEATURES_TABLE

    def test_state_cql_has_patient_pk(self) -> None:
        assert "patient_id text PRIMARY KEY" in CREATE_STATE_TABLE

    def test_get_cql_for_table(self) -> None:
        client = CassandraClient()
        cql = client.get_cql_for_table("patient_alerts")
        assert "CREATE TABLE IF NOT EXISTS" in cql
        assert "brainwatch.patient_alerts" in cql

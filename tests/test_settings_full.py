"""Tests for brainwatch.config.settings — 6 test cases."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from brainwatch.config.settings import (
    CassandraSettings,
    KafkaSettings,
    ProjectSettings,
    SparkSettings,
    default_settings,
    load_settings,
)


class TestDefaultSettings:
    def test_default_project_name(self) -> None:
        s = default_settings()
        assert s.project_name == "brainwatch"
        assert s.architecture == "lambda"

    def test_default_kafka(self) -> None:
        s = default_settings()
        assert s.kafka.eeg_topic == "eeg.raw"
        assert s.kafka.ehr_topic == "ehr.updates"

    def test_default_spark(self) -> None:
        s = default_settings()
        assert s.spark.app_name == "BrainWatch"
        assert s.spark.master == "local[*]"

    def test_default_anomaly_thresholds(self) -> None:
        s = default_settings()
        assert s.anomaly.critical_score == 0.85
        assert s.anomaly.warning_score == 0.60


class TestLoadSettings:
    def test_load_from_yaml(self, tmp_path: Path) -> None:
        yaml_content = """
project_name: test_project
architecture: lambda
kafka:
  bootstrap_servers: test:9092
  topics:
    eeg_raw: test.eeg
lakehouse:
  bronze_prefix: /test/bronze
"""
        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content, encoding="utf-8")

        settings = load_settings(config_path)
        assert settings.project_name == "test_project"
        assert settings.kafka.bootstrap_servers == "test:9092"
        assert settings.kafka.eeg_topic == "test.eeg"
        assert settings.lake.bronze_prefix == "/test/bronze"

    def test_env_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        yaml_content = """
project_name: brainwatch
kafka:
  bootstrap_servers: default:9092
"""
        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content, encoding="utf-8")

        monkeypatch.setenv("BRAINWATCH_KAFKA_BOOTSTRAP", "override:9094")
        settings = load_settings(config_path)
        assert settings.kafka.bootstrap_servers == "override:9094"

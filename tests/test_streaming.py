"""Tests for brainwatch.processing.streaming — 6 test cases (structural)."""

from __future__ import annotations

import inspect

import pytest

from brainwatch.processing import streaming


class TestStreamingImports:
    def test_build_eeg_stream_importable(self) -> None:
        assert callable(streaming.build_eeg_stream)

    def test_build_ehr_stream_importable(self) -> None:
        assert callable(streaming.build_ehr_stream)

    def test_build_joined_stream_importable(self) -> None:
        assert callable(streaming.build_joined_stream)

    def test_build_feature_aggregation_importable(self) -> None:
        assert callable(streaming.build_feature_aggregation)

    def test_build_streaming_pipeline_importable(self) -> None:
        assert callable(streaming.build_streaming_pipeline)

    def test_build_alert_pipeline_importable(self) -> None:
        assert callable(streaming.build_alert_streaming_pipeline)


class TestStreamingSignatures:
    def test_eeg_stream_params(self) -> None:
        sig = inspect.signature(streaming.build_eeg_stream)
        params = list(sig.parameters.keys())
        assert "spark" in params
        assert "kafka_servers" in params
        assert "eeg_topic" in params

    def test_pipeline_has_defaults(self) -> None:
        sig = inspect.signature(streaming.build_streaming_pipeline)
        for name, param in sig.parameters.items():
            if name != "spark":
                assert param.default != inspect.Parameter.empty, f"{name} has no default"

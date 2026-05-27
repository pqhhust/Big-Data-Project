"""Tests for brainwatch.processing.udfs — 6 test cases."""

from __future__ import annotations

import pytest

from brainwatch.processing.udfs import (
    anomaly_severity,
    compute_row_fingerprint,
    detect_electrode_disconnect,
    detect_flatline,
    detect_seizure_pattern,
    eeg_band_power,
    icd10_category,
    signal_quality_score,
)


class TestSignalQualityScore:
    def test_perfect_signal(self) -> None:
        score = signal_quality_score(21, 256.0, 1.0)
        assert score == 1.0

    def test_half_channels(self) -> None:
        score = signal_quality_score(10, 256.0, 1.0)
        assert 0.3 < score < 0.8

    def test_none_values(self) -> None:
        assert signal_quality_score(None, None, None) == 0.0


class TestAnomalySeverity:
    def test_critical(self) -> None:
        assert anomaly_severity(0.9) == "critical"

    def test_warning(self) -> None:
        assert anomaly_severity(0.7) == "warning"

    def test_normal(self) -> None:
        assert anomaly_severity(0.1) == "normal"

    def test_none(self) -> None:
        assert anomaly_severity(None) == "unknown"


class TestEEGBandPower:
    def test_returns_all_bands(self) -> None:
        bands = eeg_band_power(256.0, 21)
        assert set(bands.keys()) == {"delta", "theta", "alpha", "beta", "gamma"}

    def test_none_input(self) -> None:
        bands = eeg_band_power(None, None)
        assert all(v == 0.0 for v in bands.values())


class TestICD10Category:
    def test_valid_code(self) -> None:
        assert icd10_category("G40.0") == "G40"
        assert icd10_category("I63.9") == "I63"

    def test_no_dot(self) -> None:
        assert icd10_category("G40") == "G40"

    def test_none(self) -> None:
        assert icd10_category(None) == "UNKNOWN"


class TestDetectionFunctions:
    def test_flatline_detected(self) -> None:
        assert detect_flatline(0.0001) is True

    def test_flatline_not_detected(self) -> None:
        assert detect_flatline(0.5) is False

    def test_electrode_disconnect(self) -> None:
        assert detect_electrode_disconnect(15, 21) is True

    def test_electrode_ok(self) -> None:
        assert detect_electrode_disconnect(21, 21) is False

    def test_seizure_pattern(self) -> None:
        assert detect_seizure_pattern(0.9, 0.8) is True

    def test_seizure_low_quality(self) -> None:
        assert detect_seizure_pattern(0.9, 0.2) is False


class TestRowFingerprint:
    def test_deterministic(self) -> None:
        fp1 = compute_row_fingerprint("P001", "S001", "2024-01-01T00:00:00")
        fp2 = compute_row_fingerprint("P001", "S001", "2024-01-01T00:00:00")
        assert fp1 == fp2

    def test_different_inputs_different_fingerprints(self) -> None:
        fp1 = compute_row_fingerprint("P001", "S001", "2024-01-01T00:00:00")
        fp2 = compute_row_fingerprint("P002", "S001", "2024-01-01T00:00:00")
        assert fp1 != fp2

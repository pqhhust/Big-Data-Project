"""Tests for the deeper EEG feature extractor.

Pure-numpy synthetic signals so the math is exercised without MNE,
SciPy, or Spark installed. Each test pins one feature against a
signal with known properties.
"""
from __future__ import annotations

import math

import numpy as np

from brainwatch.processing.eeg_features import (
    BANDS,
    band_powers,
    relative_band_powers,
    hjorth_parameters,
    line_length,
    spectral_entropy,
    extract_window_features,
)


FS = 256.0   # Hz — typical clinical EEG sampling rate
DUR = 4.0    # seconds — one window
N = int(FS * DUR)


def _sine(freq_hz: float, n_channels: int = 1, amplitude: float = 1.0) -> np.ndarray:
    t = np.arange(N) / FS
    sig = amplitude * np.sin(2 * np.pi * freq_hz * t)
    if n_channels > 1:
        sig = np.tile(sig, (n_channels, 1))
    return sig


def test_band_powers_alpha_dominant_at_10hz():
    x = _sine(10.0, n_channels=4)
    p = band_powers(x, FS)
    assert max(p, key=p.get) == "alpha", \
        f"expected alpha dominant for 10 Hz sine; got {p}"


def test_band_powers_beta_dominant_at_25hz():
    x = _sine(25.0, n_channels=4)
    p = band_powers(x, FS)
    assert max(p, key=p.get) == "beta", \
        f"expected beta dominant for 25 Hz sine; got {p}"


def test_band_powers_zero_signal_returns_zeros():
    x = np.zeros((4, N))
    p = band_powers(x, FS)
    assert all(v == 0.0 for v in p.values())


def test_relative_band_powers_sum_to_one_for_nonzero_signal():
    x = _sine(10.0, n_channels=4)
    rp = relative_band_powers(x, FS)
    assert math.isclose(sum(rp.values()), 1.0, abs_tol=1e-9)


def test_relative_band_powers_zero_signal_safe():
    x = np.zeros((4, N))
    rp = relative_band_powers(x, FS)
    assert all(v == 0.0 for v in rp.values())


def test_band_keys_are_stable():
    x = _sine(10.0)
    p = band_powers(x, FS)
    assert set(p) == set(BANDS) == {"delta", "theta", "alpha", "beta", "gamma"}


def test_hjorth_activity_zero_for_flat_signal():
    x = np.zeros((4, N))
    h = hjorth_parameters(x)
    assert h["hjorth_activity"] == 0.0
    assert h["hjorth_mobility"] == 0.0
    assert h["hjorth_complexity"] == 0.0


def test_hjorth_activity_positive_for_noise():
    rng = np.random.default_rng(0xBEEF)
    x = rng.standard_normal((4, N))
    h = hjorth_parameters(x)
    assert h["hjorth_activity"] > 0.0
    assert h["hjorth_mobility"] > 0.0


def test_hjorth_keys_are_stable():
    x = _sine(10.0)
    h = hjorth_parameters(x)
    assert set(h) == {"hjorth_activity", "hjorth_mobility", "hjorth_complexity"}


def test_line_length_zero_for_flat_signal():
    x = np.zeros((4, N))
    assert line_length(x) == 0.0


def test_line_length_scales_with_amplitude():
    x_small = _sine(10.0, amplitude=1.0)
    x_large = _sine(10.0, amplitude=10.0)
    assert line_length(x_large) > line_length(x_small)
    assert math.isclose(
        line_length(x_large) / max(line_length(x_small), 1e-12),
        10.0, rel_tol=1e-6
    )


def test_line_length_scales_with_frequency():
    x_slow = _sine(2.0)
    x_fast = _sine(20.0)
    assert line_length(x_fast) > line_length(x_slow)


def test_spectral_entropy_low_for_pure_sine():
    x = _sine(10.0, n_channels=4)
    ent = spectral_entropy(x, FS)
    assert 0.0 <= ent < 2.0


def test_spectral_entropy_high_for_white_noise():
    rng = np.random.default_rng(0xC0FFEE)
    x = rng.standard_normal((4, N))
    ent = spectral_entropy(x, FS)
    assert ent > 8.0


def test_extract_window_features_returns_stable_schema():
    feats = extract_window_features(_sine(10.0), FS)
    keys = set(feats)
    expected = {
        "line_length", "spectral_entropy",
        "hjorth_activity", "hjorth_mobility", "hjorth_complexity",
        "power_delta", "power_theta", "power_alpha", "power_beta", "power_gamma",
        "rel_power_delta", "rel_power_theta", "rel_power_alpha",
        "rel_power_beta", "rel_power_gamma",
    }
    assert keys == expected, f"unexpected keys: {keys ^ expected}"


def test_extract_window_features_all_floats_no_nan():
    feats = extract_window_features(_sine(10.0), FS)
    for k, v in feats.items():
        assert isinstance(v, float), f"{k} is not float: {type(v).__name__}"
        assert math.isfinite(v), f"{k} is not finite: {v}"

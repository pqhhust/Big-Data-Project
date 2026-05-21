"""Tests for the measured signal-quality function in scripts/edf_to_bronze.py."""
from __future__ import annotations

import numpy as np

from scripts.edf_to_bronze import _quality


def test_empty_window_is_worst_quality():
    amp, flat, clip, q = _quality(np.empty((0, 0)))
    assert q == 0.0
    assert flat == 1.0


def test_clean_signal_has_high_quality():
    rng = np.random.default_rng(0)
    # 20 channels × 2000 samples of ~50 µV noise (volts → 50e-6)
    window = rng.normal(0, 50e-6, size=(20, 2000))
    amp, flat, clip, q = _quality(window)
    assert flat == 0.0          # nothing flat
    assert q > 0.9
    assert amp > 0              # measured amplitude in µV


def test_all_flat_channels_drop_quality():
    window = np.zeros((20, 2000))
    amp, flat, clip, q = _quality(window)
    assert flat == 1.0
    assert q == 0.0


def test_half_flat_channels():
    rng = np.random.default_rng(1)
    good = rng.normal(0, 50e-6, size=(10, 2000))
    bad = np.zeros((10, 2000))
    window = np.vstack([good, bad])
    amp, flat, clip, q = _quality(window)
    assert flat == 0.5
    assert 0.0 <= q <= 0.5


def test_amplitude_reported_in_microvolts():
    window = np.full((4, 100), 100e-6)  # constant 100 µV → flat though
    amp, flat, clip, q = _quality(window)
    assert amp == 100.0  # 100e-6 V → 100 µV


def test_quality_is_bounded_unit_interval():
    rng = np.random.default_rng(2)
    for scale in (1e-6, 50e-6, 1e-3):
        window = rng.normal(0, scale, size=(8, 500))
        _, _, _, q = _quality(window)
        assert 0.0 <= q <= 1.0

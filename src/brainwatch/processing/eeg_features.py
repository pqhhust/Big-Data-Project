"""Deeper per-window EEG features for the gold/batch path.

The speed layer reaches anomaly scores from a small set of windowed
EEG features (chunk count, mean sampling rate, signal quality). This
module produces a richer feature row per window that the *batch* path
materialises into the gold zone and feeds into a downstream
``compute_anomaly_score_v3`` (in :mod:`brainwatch.serving.anomaly_rules`,
to be added) or a Spark MLlib classifier.

All functions are pure NumPy, so the math is testable without MNE,
SciPy, or Spark installed. EDF I/O lives at the script boundary
(:file:`scripts/extract_eeg_features.py`) and feeds windowed arrays
into the functions below.

Features computed per window:

- **band_powers(x, fs)** → absolute power in each of the five
  clinical bands (delta 0.5–4 Hz, theta 4–8 Hz, alpha 8–13 Hz,
  beta 13–30 Hz, gamma 30–45 Hz).
- **relative_band_powers(x, fs)** → the same five, normalised to sum
  to 1 (good as feature inputs because they are scale-invariant).
- **hjorth_parameters(x)** → (activity, mobility, complexity), the
  classical seizure-and-state indicators from Hjorth (1970).
- **line_length(x)** → sum |x_{i+1} − x_i|; a robust early-warning
  feature for ictal and interictal-continuum activity.
- **spectral_entropy(x, fs)** → Shannon entropy of the normalised
  power spectrum, base 2, in bits. Bounded by log2(N/2). Higher
  entropy means a less rhythmic, more noise-like signal.
- **extract_window_features(x, fs)** → a single dict carrying every
  feature above, ready to be written into a row of the gold zone
  or fed into a Spark UDF.

A multi-channel array (n_channels, n_samples) is reduced to a single
feature row by averaging across channels (an opinionated choice — it
is fine for the dashboard's per-window roll-up and keeps the gold row
narrow; a per-channel fan-out is a future extension).
"""
from __future__ import annotations

from typing import Any

import numpy as np


# Default clinical EEG band edges, in Hz.
BANDS = {
    "delta": (0.5, 4.0),
    "theta": (4.0, 8.0),
    "alpha": (8.0, 13.0),
    "beta":  (13.0, 30.0),
    "gamma": (30.0, 45.0),
}


def _as_2d(x: np.ndarray) -> np.ndarray:
    """Promote a 1-D signal to (1, n_samples); pass 2-D through."""
    x = np.asarray(x)
    if x.ndim == 1:
        x = x[np.newaxis, :]
    elif x.ndim != 2:
        raise ValueError(
            f"signal must be 1-D or 2-D (n_channels, n_samples); got shape {x.shape}")
    return x


def band_powers(x: np.ndarray, fs: float,
                bands: dict[str, tuple[float, float]] | None = None) -> dict[str, float]:
    """Absolute power per band, averaged across channels if x is 2-D.

    Implementation: rectangular window + rFFT power spectrum + sum
    over band-edges. (A Hanning window is the textbook recommendation
    but on a 30-second window of a well-behaved EEG it is a small
    correction; we keep the code dependency-free.)
    """
    x = _as_2d(x)
    fs = float(fs)
    bands = bands or BANDS
    n = x.shape[1]
    if n < 2 or fs <= 0:
        return {b: 0.0 for b in bands}
    # rFFT power: shape (n_channels, n_freqs)
    psd = (np.abs(np.fft.rfft(x, axis=1)) ** 2) / n
    freqs = np.fft.rfftfreq(n, d=1.0 / fs)
    out: dict[str, float] = {}
    for name, (lo, hi) in bands.items():
        mask = (freqs >= lo) & (freqs < hi)
        if not mask.any():
            out[name] = 0.0
        else:
            # Average across channels, sum across frequencies in the band.
            out[name] = float(psd[:, mask].sum(axis=1).mean())
    return out


def relative_band_powers(x: np.ndarray, fs: float,
                         bands: dict[str, tuple[float, float]] | None = None
                         ) -> dict[str, float]:
    """Scale-invariant band powers — each value in [0, 1], sum = 1."""
    raw = band_powers(x, fs, bands=bands)
    total = sum(raw.values())
    if total <= 0.0:
        return {k: 0.0 for k in raw}
    return {k: v / total for k, v in raw.items()}


def hjorth_parameters(x: np.ndarray) -> dict[str, float]:
    """(activity, mobility, complexity) per Hjorth (1970).

    activity   = var(x)
    mobility   = sqrt(var(dx) / var(x))
    complexity = mobility(dx) / mobility(x)

    Channel reduction: features are computed per channel, then averaged.
    """
    x = _as_2d(x).astype(float, copy=False)
    eps = 1e-12

    def _per_channel(c: np.ndarray) -> tuple[float, float, float]:
        var0 = float(np.var(c))
        if var0 <= eps:
            return 0.0, 0.0, 0.0
        d1 = np.diff(c)
        var1 = float(np.var(d1)) if d1.size else 0.0
        mob = float(np.sqrt(var1 / var0)) if var0 > eps else 0.0
        if d1.size < 2 or var1 <= eps:
            return var0, mob, 0.0
        d2 = np.diff(d1)
        var2 = float(np.var(d2)) if d2.size else 0.0
        mob1 = float(np.sqrt(var2 / var1)) if var1 > eps else 0.0
        complexity = mob1 / mob if mob > eps else 0.0
        return var0, mob, complexity

    vals = np.array([_per_channel(x[ch]) for ch in range(x.shape[0])])
    return {
        "hjorth_activity":   float(vals[:, 0].mean()),
        "hjorth_mobility":   float(vals[:, 1].mean()),
        "hjorth_complexity": float(vals[:, 2].mean()),
    }


def line_length(x: np.ndarray) -> float:
    """Sum of |Δ x| across the window, averaged across channels.

    Robust early-warning feature for ictal / IIC activity; almost
    insensitive to baseline drift, sensitive to high-frequency bursts.
    """
    x = _as_2d(x).astype(float, copy=False)
    if x.shape[1] < 2:
        return 0.0
    return float(np.abs(np.diff(x, axis=1)).sum(axis=1).mean())


def spectral_entropy(x: np.ndarray, fs: float) -> float:
    """Shannon entropy of the normalised power spectrum, base 2.

    Returns a value in [0, log2(n_freqs)]: 0 means all power in one
    frequency bin (pure tone); maximum means power is spread uniformly
    across the spectrum (white noise). Averaged across channels.
    """
    x = _as_2d(x).astype(float, copy=False)
    n = x.shape[1]
    if n < 2 or fs <= 0:
        return 0.0
    psd = (np.abs(np.fft.rfft(x, axis=1)) ** 2) / n  # (n_channels, n_freqs)
    eps = 1e-12
    entropies: list[float] = []
    for ch in range(x.shape[0]):
        p = psd[ch] + eps
        p = p / p.sum()
        entropies.append(float(-(p * np.log2(p)).sum()))
    return float(np.mean(entropies))


def extract_window_features(x: np.ndarray, fs: float) -> dict[str, Any]:
    """One-call extractor that returns every feature in this module.

    Use from a Spark UDF, a Pandas UDF, or
    ``scripts/extract_eeg_features.py`` (which adds the MNE EDF read on
    top). The returned dict shape is stable: keys appear in the same
    order on every call, and every value is a plain Python float so
    Spark can infer a DoubleType column directly.
    """
    feats: dict[str, Any] = {}
    feats["line_length"] = line_length(x)
    feats["spectral_entropy"] = spectral_entropy(x, fs)
    feats.update(hjorth_parameters(x))
    # Absolute and relative band powers as a single flat dict so they
    # map onto Spark columns one-to-one.
    for name, value in band_powers(x, fs).items():
        feats[f"power_{name}"] = float(value)
    for name, value in relative_band_powers(x, fs).items():
        feats[f"rel_power_{name}"] = float(value)
    return feats

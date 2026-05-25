#!/usr/bin/env python3
"""Ablation harness for BrainWatch anomaly-scoring hyperparameters.

The BrainWatch codebase ships two anomaly-score formulas:

  (i)  The 2-term INLINE formula in src/brainwatch/processing/speed_layer.py
       (`_score`):
           base   = 0.60 * chunk_term + 0.40 * quality_term
           score  = clamp(base + variance, 0, 1),  variance in [-0.25, +0.25]
                   from a deterministic CRC32 hash of (patient_id, win_start).

  (ii) The 4-term V2 FORMULA in src/brainwatch/serving/anomaly_rules.py
       (`compute_anomaly_score`):
           score  = clamp(0.30 * chunk + 0.25 * quality
                         + 0.30 * critical + 0.15 * meds, 0, 1)

Formula (i) is deployed; formula (ii) is the richer scoring planned for
the next iteration once an EHR-aware feature row reaches the speed
layer. Both are studied below.

The v2 classifier in anomaly_rules.classify_v2 maps a score to one of
five severities:
    critical   if has_critical_lab and score >= 0.60
    critical   if score >= 0.85
    warning    if score >= 0.65
    advisory   if score >= 0.40
    normal     otherwise
plus a pre-classifier suppression rule applied by the streaming sink:
    suppressed if signal_quality < 0.30

Studies:
   A1  Speed-layer (i) weight ablation     (vary chunk/quality split)
   A2  V2 formula  (ii) weight ablation    (vary all four weights)
   B   Severity threshold sensitivity      (vary the (advisory, warn,
                                            critical) triple against
                                            the shipped v2 formula)

Inputs are a deterministic synthetic mix of N windows resembling the
live demo: realistic ranges for eeg_chunk_count (Gaussian with a 12%
storm tail), signal_quality_score (bimodal at 0.20 and 0.85),
has_critical_lab (10% base rate, elevated to 35% inside the storm
tail), n_medication_changes_24h (exponential, capped at 10).

Usage:
    python scripts/ablate_anomaly_hyperparams.py            # both tables
    python scripts/ablate_anomaly_hyperparams.py --n 5000
"""
from __future__ import annotations

import argparse
import random
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from brainwatch.serving.anomaly_rules import classify_v2  # noqa: E402


def make_windows(n: int, seed: int = 0xBEEF) -> list[dict]:
    rng = random.Random(seed)
    windows = []
    for i in range(n):
        storm = rng.random() < 0.12
        if storm:
            eeg_chunks = int(rng.uniform(45, 110))
            quality = max(0.0, min(1.0, rng.gauss(0.55, 0.15)))
            critical = rng.random() < 0.35
        else:
            eeg_chunks = max(0, int(rng.gauss(20, 8)))
            quality = (max(0.0, min(1.0, rng.gauss(0.20, 0.10)))
                       if rng.random() < 0.18
                       else max(0.0, min(1.0, rng.gauss(0.88, 0.06))))
            critical = rng.random() < 0.10
        meds = min(int(rng.expovariate(1 / 1.5)), 10)
        windows.append({
            "patient_id": f"P{i % 100:03d}",
            "win_start": i,
            "eeg_chunk_count": eeg_chunks,
            "signal_quality_score": quality,
            "has_critical_lab": critical,
            "n_medication_changes_24h": meds,
        })
    return windows


def shipped_score(f: dict, w_chunk: float, w_quality: float) -> float:
    """Mirrors speed_layer._score with parameterised (chunk, quality) weights."""
    chunk_count = int(f["eeg_chunk_count"])
    signal_quality = max(0.0, min(1.0, f["signal_quality_score"]))
    chunk_term = min(chunk_count / 25.0, 1.0)
    quality_term = 1.0 - signal_quality
    base = w_chunk * chunk_term + w_quality * quality_term
    key = f"{f['patient_id']}|{int(f['win_start'])}".encode("utf-8")
    h = (zlib.crc32(key) & 0xffffffff) / 0xffffffff
    variance = (h - 0.5) * 0.5
    return max(0.0, min(base + variance, 1.0))


def v2_score(f: dict, w: tuple[float, float, float, float]) -> float:
    chunk_term    = min(f["eeg_chunk_count"] / 60.0, 1.0)
    quality_term  = 1.0 - f["signal_quality_score"]
    critical_term = 0.6 if f["has_critical_lab"] else 0.0
    meds_term     = min(f["n_medication_changes_24h"] / 5.0, 1.0)
    s = w[0]*chunk_term + w[1]*quality_term + w[2]*critical_term + w[3]*meds_term
    return max(0.0, min(s, 1.0))


def classify_param(score: float, has_critical_lab: bool,
                   t_advisory=0.40, t_warning=0.65,
                   t_critical=0.85, t_crit_lab_floor=0.60) -> str:
    """Local parameterised mirror of anomaly_rules.classify_v2."""
    if has_critical_lab and score >= t_crit_lab_floor:
        return "critical"
    if score >= t_critical:
        return "critical"
    if score >= t_warning:
        return "warning"
    if score >= t_advisory:
        return "advisory"
    return "normal"


SEVERITIES = ("suppressed", "normal", "advisory", "warning", "critical")


def severity_distribution(windows, scoring_fn, *, t_adv=0.40, t_warn=0.65,
                          t_crit=0.85, t_crit_lab=0.60) -> dict[str, int]:
    counts = {s: 0 for s in SEVERITIES}
    for f in windows:
        if f["signal_quality_score"] < 0.30:
            counts["suppressed"] += 1
            continue
        s = scoring_fn(f)
        sev = classify_param(s, bool(f["has_critical_lab"]),
                             t_advisory=t_adv, t_warning=t_warn,
                             t_critical=t_crit, t_crit_lab_floor=t_crit_lab)
        counts[sev] += 1
    return counts


def pct(d):
    total = max(1, sum(d.values()))
    return {k: 100.0 * v / total for k, v in d.items()}


SHIPPED_VARIANTS = [
    ("Baseline (deployed)", (0.60, 0.40)),
    ("Chunk-only",          (1.00, 0.00)),
    ("Quality-only",        (0.00, 1.00)),
    ("Equal split",         (0.50, 0.50)),
    ("Quality-heavy",       (0.30, 0.70)),
    ("Chunk-heavy",         (0.80, 0.20)),
]

V2_VARIANTS = [
    ("Baseline (anomaly_rules)", (0.30, 0.25, 0.30, 0.15)),
    ("All-equal",                (0.25, 0.25, 0.25, 0.25)),
    ("Chunk-heavy",              (0.50, 0.20, 0.20, 0.10)),
    ("Critical-heavy",           (0.20, 0.20, 0.45, 0.15)),
    ("No critical-lab term",     (0.45, 0.30, 0.00, 0.25)),
    ("Quality-heavy",            (0.20, 0.50, 0.20, 0.10)),
]

THRESHOLD_GRID = [
    # (t_advisory, t_warning, t_critical)
    (0.30, 0.55, 0.80),
    (0.30, 0.65, 0.85),
    (0.40, 0.55, 0.80),
    (0.40, 0.65, 0.85),   # baseline
    (0.40, 0.65, 0.90),
    (0.50, 0.70, 0.85),
    (0.50, 0.75, 0.90),
]


def hline(n): print("-" * n)


def print_shipped_weight_table(windows):
    print()
    print(f"# A1. Speed-layer inline weight ablation (N = {len(windows)})")
    print("variant                   (chunk, quality)   suppr  normal advis  warn   crit")
    hline(78)
    for name, (wc, wq) in SHIPPED_VARIANTS:
        p = pct(severity_distribution(windows, lambda f: shipped_score(f, wc, wq)))
        print(f"{name:<26}({wc:.2f}, {wq:.2f})         "
              f"{p['suppressed']:>5.1f}%{p['normal']:>7.1f}%"
              f"{p['advisory']:>6.1f}%{p['warning']:>6.1f}%{p['critical']:>6.1f}%")


def print_v2_weight_table(windows):
    print()
    print(f"# A2. v2 formula (anomaly_rules) weight ablation (N = {len(windows)})")
    print("variant                   (chunk,quality,critical,meds)    suppr  normal advis  warn   crit")
    hline(91)
    for name, w in V2_VARIANTS:
        p = pct(severity_distribution(windows, lambda f: v2_score(f, w)))
        wstr = f"({w[0]:.2f},{w[1]:.2f},{w[2]:.2f},{w[3]:.2f})"
        print(f"{name:<26}{wstr:<30}{p['suppressed']:>5.1f}%"
              f"{p['normal']:>7.1f}%{p['advisory']:>6.1f}%"
              f"{p['warning']:>6.1f}%{p['critical']:>6.1f}%")


def print_threshold_table(windows):
    baseline_w = V2_VARIANTS[0][1]
    print()
    print(f"# B. Severity threshold sensitivity, baseline v2 weights, N = {len(windows)}")
    print(" adv   warn   crit    suppr   normal  advis   warn   crit")
    hline(60)
    for adv, warn, crit in THRESHOLD_GRID:
        p = pct(severity_distribution(windows, lambda f: v2_score(f, baseline_w),
                                       t_adv=adv, t_warn=warn, t_crit=crit))
        mark = "  <- baseline" if (adv, warn, crit) == (0.40, 0.65, 0.85) else ""
        print(f"{adv:>4.2f}  {warn:>4.2f}  {crit:>4.2f}  "
              f"{p['suppressed']:>6.1f}%{p['normal']:>8.1f}%"
              f"{p['advisory']:>7.1f}%{p['warning']:>6.1f}%"
              f"{p['critical']:>6.1f}%{mark}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=0xBEEF)
    args = ap.parse_args()

    windows = make_windows(args.n, seed=args.seed)
    # Sanity check the shipped classifier matches our local parameterised mirror at
    # the baseline thresholds.
    s = v2_score(windows[0], V2_VARIANTS[0][1])
    expected = classify_v2(s, bool(windows[0]["has_critical_lab"])).severity
    got = classify_param(s, bool(windows[0]["has_critical_lab"]))
    assert expected == got, f"classifier divergence: shipped={expected} mirror={got}"

    print_shipped_weight_table(windows)
    print_v2_weight_table(windows)
    print_threshold_table(windows)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Live alerts feeder for the dashboard demo.

Appends new realistic alerts to the JSONL file every few seconds, so a
dashboard polling that file sees new rows arrive in near-real time.

Usage::

    python scripts/live_alerts_feeder.py \
        --out dashboard/public/alerts_export.jsonl \
        --interval 2.0 \
        --burst 1-3
"""
from __future__ import annotations

import argparse
import json
import random
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from brainwatch.serving.anomaly_rules import compute_anomaly_score, classify_v2


_EVENT_TYPES = ("vital_signs", "lab_result", "medication", "critical_lab")


def _random_features(rng: random.Random) -> dict:
    """Make the live feed visibly interesting — bias scores toward warning/critical."""
    bucket = rng.random()
    if bucket < 0.10:
        # critical-spike scenario
        return {
            "eeg_chunk_count": rng.randint(80, 200),
            "signal_quality_score": max(0.10, rng.gauss(0.30, 0.10)),
            "has_critical_lab": True,
            "n_medication_changes_24h": rng.randint(2, 6),
        }
    if bucket < 0.30:
        return {
            "eeg_chunk_count": rng.randint(40, 120),
            "signal_quality_score": max(0.20, rng.gauss(0.55, 0.10)),
            "has_critical_lab": False,
            "n_medication_changes_24h": rng.randint(1, 4),
        }
    if bucket < 0.65:
        return {
            "eeg_chunk_count": rng.randint(10, 60),
            "signal_quality_score": max(0.30, rng.gauss(0.70, 0.10)),
            "has_critical_lab": False,
            "n_medication_changes_24h": rng.randint(0, 2),
        }
    # normal
    return {
        "eeg_chunk_count": rng.randint(0, 30),
        "signal_quality_score": max(0.40, rng.gauss(0.85, 0.08)),
        "has_critical_lab": False,
        "n_medication_changes_24h": rng.randint(0, 1),
    }


def _parse_range(spec: str) -> tuple[int, int]:
    if "-" in spec:
        lo, hi = spec.split("-", 1)
        return int(lo), int(hi)
    n = int(spec)
    return n, n


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("dashboard/public/alerts_export.jsonl"))
    parser.add_argument("--interval", type=float, default=2.0, help="seconds between bursts")
    parser.add_argument("--burst", default="1-3", help="alerts per burst, e.g. 1-3")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--max-rows", type=int, default=100_000,
                        help="trim the file once it grows past this many rows")
    parser.add_argument("--patients", type=int, default=120,
                        help="size of the rotating patient pool")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    burst_lo, burst_hi = _parse_range(args.burst)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    if not args.out.exists():
        args.out.touch()

    patient_pool = [f"sub-LIVE{rng.randint(10**6, 10**7 - 1)}-{i:03d}" for i in range(args.patients)]

    stopped = False

    def _stop(signum, frame):
        nonlocal stopped
        stopped = True
        print("\n[feeder] stop signal received, exiting after current burst...")
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    print(f"[feeder] writing to {args.out} every {args.interval}s "
          f"(burst {burst_lo}-{burst_hi}, pool {len(patient_pool)} patients)")
    total = 0
    try:
        while not stopped:
            n = rng.randint(burst_lo, burst_hi)
            now = datetime.now(timezone.utc).isoformat()
            new_lines = []
            for _ in range(n):
                features = _random_features(rng)
                score = compute_anomaly_score(features)
                if features["signal_quality_score"] < 0.30:
                    severity = "suppressed"
                else:
                    severity = classify_v2(score, features["has_critical_lab"]).severity
                row = {
                    "patient_id": rng.choice(patient_pool),
                    "alert_time": now,
                    "severity": severity,
                    "anomaly_score": round(score, 4),
                    "signal_quality_score": round(features["signal_quality_score"], 4),
                    "has_critical_lab": features["has_critical_lab"],
                    "n_eeg_chunks": features["eeg_chunk_count"],
                    "n_medication_changes_24h": features["n_medication_changes_24h"],
                    "explanation": (
                        f"score={score:.2f}; quality={features['signal_quality_score']:.2f}; "
                        f"critical_lab={features['has_critical_lab']}"
                    ),
                }
                new_lines.append(json.dumps(row))
            with args.out.open("a") as f:
                f.write("\n".join(new_lines) + "\n")
            total += n
            print(f"[feeder] +{n} alerts (total this run: {total})  →  {new_lines[-1][:90]}...")

            # cheap rotation so the file never grows unbounded
            if total % 500 == 0:
                lines = args.out.read_text().splitlines()
                if len(lines) > args.max_rows:
                    keep = lines[-args.max_rows:]
                    args.out.write_text("\n".join(keep) + "\n")
                    print(f"[feeder] trimmed file to last {args.max_rows} rows")

            time.sleep(args.interval)
    finally:
        print(f"[feeder] done — emitted {total} alerts this run")
    return 0


if __name__ == "__main__":
    sys.exit(main())

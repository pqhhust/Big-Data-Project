#!/usr/bin/env python3
"""Generate a realistic alerts dataset from the gold/patient_features Parquet.

Reads the gold patient_features table, applies the production
``compute_anomaly_score`` + ``classify_v2`` rules, and emits an alerts JSONL
file consumable by the dashboard.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from brainwatch.serving.anomaly_rules import compute_anomaly_score, classify_v2


def _row_to_features(row: dict, rng: random.Random) -> dict:
    """Synthesize realistic feature inputs for a patient/day row.

    Pure gold-rollup numbers are too uniform to drive an interesting dashboard
    (uniform synthetic generator → no rare critical events). We inject a
    plausible severity mix here for the demo: ~3% critical, ~12% warning,
    ~25% advisory, ~57% normal, ~3% suppressed.
    """
    base_chunks = int(row.get("n_eeg_chunks", 0))
    eeg_chunk_count = base_chunks + rng.randint(0, 80)
    signal_quality = max(0.05, min(1.0, rng.gauss(0.72, 0.18)))
    has_critical_lab = rng.random() < 0.06
    n_med_changes = max(0, int(rng.gauss(1.0, 2.0)))
    if rng.random() < 0.10:
        eeg_chunk_count += 40
        n_med_changes += 3
    return {
        "eeg_chunk_count": eeg_chunk_count,
        "signal_quality_score": signal_quality,
        "has_critical_lab": has_critical_lab,
        "n_medication_changes_24h": n_med_changes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, default=Path("data/lake/gold"))
    parser.add_argument("--out", type=Path, default=Path("artifacts/demo/alerts_export.jsonl"))
    parser.add_argument("--limit", type=int, default=2000,
                        help="Cap the number of patient/day rows we scan")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    from pyspark.sql import SparkSession

    spark = (
        SparkSession.builder.appName("brainwatch-alerts")
        .master("local[4]")
        .config("spark.driver.memory", "4g")
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("ERROR")

    rows = (
        spark.read.parquet(str(args.gold / "patient_features"))
        .limit(args.limit)
        .collect()
    )
    spark.stop()

    rng = random.Random(args.seed)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    n_written = {"critical": 0, "warning": 0, "advisory": 0, "normal": 0, "suppressed": 0}
    base_time = datetime(2026, 5, 19, 8, 0, 0, tzinfo=timezone.utc)

    with args.out.open("w") as f:
        for i, row in enumerate(rows):
            features = _row_to_features(row.asDict(), rng)
            score = compute_anomaly_score(features)
            if features["signal_quality_score"] < 0.30:
                severity = "suppressed"
            else:
                decision = classify_v2(score, features["has_critical_lab"])
                severity = decision.severity
            alert_time = (base_time + timedelta(minutes=i * 3 + rng.randint(0, 60))).isoformat()
            record = {
                "patient_id": str(row["patient_id"]),
                "alert_time": alert_time,
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
            f.write(json.dumps(record) + "\n")
            n_written[severity] += 1

    print(json.dumps({"written": sum(n_written.values()), "by_severity": n_written,
                      "out": str(args.out)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

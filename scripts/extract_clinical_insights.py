#!/usr/bin/env python3
"""Extract clinical insights from gold layer and generate alerts.

Usage:
    python scripts/extract_clinical_insights.py \
        --silver data/lake/silver_real \
        --gold data/lake/gold_real \
        --alerts artifacts/demo/alerts_real.jsonl
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract clinical insights and generate alerts.")
    parser.add_argument("--silver", required=True, help="Silver data directory")
    parser.add_argument("--gold", required=True, help="Gold data directory")
    parser.add_argument("--alerts", required=True, help="Output alerts JSONL path")
    parser.add_argument("--threshold", type=float, default=0.6, help="Anomaly score threshold for alerts")
    return parser


def extract_insights_local(silver_path: str, gold_path: str, threshold: float) -> list[dict]:
    """Extract insights from local JSONL files (no Spark required)."""
    from brainwatch.serving.anomaly_rules import evaluate_all_rules, AnomalyThresholds
    from brainwatch.contracts.events import AlertEvent

    alerts = []
    thresholds = AnomalyThresholds(warning_score=threshold)

    # Read silver EEG records
    silver_dir = Path(silver_path)
    for jsonl_file in sorted(silver_dir.glob("*.jsonl")):
        with jsonl_file.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    # Compute features
                    from brainwatch.processing.udfs import signal_quality_score
                    sq = signal_quality_score(
                        record.get("channel_count"),
                        record.get("sampling_rate_hz"),
                        record.get("window_seconds"),
                    )

                    # Simple anomaly score heuristic
                    anomaly = 0.1 + (1.0 - sq) * 0.5
                    if record.get("event_type") == "critical_lab":
                        anomaly += 0.4

                    # Evaluate rules
                    decision = evaluate_all_rules(
                        anomaly_score=anomaly,
                        signal_quality_score=sq,
                        channel_count=record.get("channel_count"),
                        thresholds=thresholds,
                    )

                    if decision.severity not in ("normal", "suppressed"):
                        alert = AlertEvent(
                            patient_id=record.get("patient_id", "unknown"),
                            session_id=record.get("session_id", "unknown"),
                            alert_time=datetime.now(timezone.utc).isoformat(),
                            severity=decision.severity,
                            anomaly_score=anomaly,
                            explanation=decision.explanation,
                        )
                        alerts.append(alert.to_dict())

                except (json.JSONDecodeError, KeyError) as exc:
                    logger.debug("Skipping record: %s", exc)

    return alerts


def main() -> None:
    args = build_parser().parse_args()

    alerts_path = Path(args.alerts)
    alerts_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Extracting clinical insights from %s + %s", args.silver, args.gold)

    alerts = extract_insights_local(args.silver, args.gold, args.threshold)

    # Write alerts
    with alerts_path.open("w", encoding="utf-8") as fh:
        for alert in alerts:
            fh.write(json.dumps(alert, default=str) + "\n")

    # Summary statistics
    severity_counts = {}
    for a in alerts:
        sev = a.get("severity", "unknown")
        severity_counts[sev] = severity_counts.get(sev, 0) + 1

    summary = {
        "total_alerts": len(alerts),
        "severity_distribution": severity_counts,
        "output_path": str(alerts_path),
        "threshold": args.threshold,
    }

    logger.info("Clinical insights extracted: %d alerts", len(alerts))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

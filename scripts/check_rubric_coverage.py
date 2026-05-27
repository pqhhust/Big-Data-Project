#!/usr/bin/env python3
"""Programmatic alignment check between the IT4043E rubric and the repo.

Reads ``docs/RUBRIC-COVERAGE.md`` (the canonical mapping doc), then
for every file path cited there confirms the file exists on disk.
Also asserts that every rubric topic from the HTML rubric is named
at least once in the coverage doc. Exits non-zero if any check fails.

Run from the repo root:

    python scripts/check_rubric_coverage.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
COVERAGE = REPO / "docs" / "RUBRIC-COVERAGE.md"

REQUIRED_RUBRIC_TOPICS = [
    "Apache Spark", "HDFS", "Apache Kafka", "Cassandra", "EKS",
    "Complex aggregations", "Advanced transformations",
    "Join operations", "Performance optimisation",
    "Stream processing", "Advanced analytics",
    "Data Ingestion", "Data Processing with Spark",
    "Stream Processing", "Data Storage", "System Integration",
    "Performance Optimization", "Monitoring", "Scaling",
    "Data Quality", "Security", "Fault Tolerance",
]


def extract_file_anchors(text: str) -> list[str]:
    out: list[str] = []
    for m in re.finditer(r"`([a-zA-Z0-9_./-]+\.(?:py|sh|tex|yaml|md|txt))`", text):
        out.append(m.group(1))
    return sorted(set(out))


def main() -> int:
    if not COVERAGE.exists():
        print(f"FAIL: {COVERAGE} does not exist", file=sys.stderr)
        return 1
    text = COVERAGE.read_text()

    missing_topics = [t for t in REQUIRED_RUBRIC_TOPICS if t not in text]
    if missing_topics:
        print("FAIL: rubric topics missing from coverage doc:")
        for t in missing_topics:
            print(f"  - {t}")
        return 1
    print(f"OK: all {len(REQUIRED_RUBRIC_TOPICS)} rubric topics referenced")

    anchors = extract_file_anchors(text)
    missing_files = []
    for a in anchors:
        if a.startswith("http") or "/" not in a:
            continue
        if not (REPO / a).exists():
            missing_files.append(a)
    if missing_files:
        print(f"FAIL: {len(missing_files)} cited files do not exist:")
        for a in missing_files:
            print(f"  - {a}")
        return 1
    print(f"OK: all {len(anchors)} cited file anchors exist on disk")

    must_exist = [
        "src/brainwatch/processing/speed_layer.py",
        "src/brainwatch/processing/gold_layer.py",
        "src/brainwatch/processing/silver_layer.py",
        "src/brainwatch/processing/eeg_features.py",
        "src/brainwatch/serving/cassandra_sink.py",
        "src/brainwatch/serving/anomaly_rules.py",
        "src/brainwatch/ingestion/bronze_writer.py",
        "src/brainwatch/ingestion/kafka_helpers.py",
        "infra/cloud/k8s-overlays/hdfs.yaml",
        "infra/cloud/k8s-overlays/kafka-kraft.yaml",
        "infra/cloud/k8s-overlays/batch-on-hdfs.yaml",
        "infra/cloud/resume_from_snapshots.sh",
        "scripts/verify_exactly_once.sh",
        "scripts/ablate_anomaly_hyperparams.py",
        "scripts/extract_eeg_features.py",
        "scripts/end_to_end_local.sh",
        "artifacts/eks/snapshots/index.txt",
        "tests/test_serving_enrichment.py",
        "tests/test_eeg_features.py",
        "tests/test_speed_layer_end_to_end.py",
    ]
    spot_missing = [p for p in must_exist if not (REPO / p).exists()]
    if spot_missing:
        print("FAIL: critical pipeline files missing:")
        for p in spot_missing:
            print(f"  - {p}")
        return 1
    print(f"OK: all {len(must_exist)} critical pipeline files present")

    print()
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

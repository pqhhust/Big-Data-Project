#!/usr/bin/env python3
"""Daily batch driver: bronze → silver → gold.

Invoked by the Kubernetes CronJob (``infra/k8s/spark-batch-cronjob.yaml``)
and runnable locally via ``python scripts/run_batch.py``.
"""
from __future__ import annotations

import argparse
import os
import sys
import time


def main() -> int:
    # LAKE_BASE lets the K8s overlay flip the whole pipeline between
    # local file paths (`data/lake`) and the hybrid HDFS overlay
    # (`hdfs://hdfs-namenode-0.hdfs-namenode.brainwatch.svc.cluster.local:8020/lake`)
    # without code changes. Explicit --bronze/--silver/--gold still override.
    lake_base = os.environ.get("LAKE_BASE", "data/lake").rstrip("/")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bronze", default=f"{lake_base}/bronze")
    parser.add_argument("--silver", default=f"{lake_base}/silver")
    parser.add_argument("--gold", default=f"{lake_base}/gold")
    parser.add_argument("--alerts-export", default=None,
                        help="Optional path to alerts JSONL export for the gold summary table")
    parser.add_argument("--app-name", default="brainwatch-batch")
    parser.add_argument("--master", default="local[16]")
    parser.add_argument("--driver-memory", default="24g")
    parser.add_argument("--shuffle-partitions", default="256")
    parser.add_argument("--local-dir", default=None,
                        help="Spark scratch dir (defaults to system /tmp)")
    args = parser.parse_args()

    from pyspark.sql import SparkSession
    from brainwatch.processing.silver_layer import (
        build_eeg_silver, build_ehr_silver, build_patient_dim,
    )
    from brainwatch.processing.gold_layer import (
        build_patient_features, build_alert_summary,
    )

    builder = (
        SparkSession.builder
        .appName(args.app_name)
        .master(args.master)
        .config("spark.driver.memory", args.driver_memory)
        .config("spark.driver.maxResultSize", "4g")
        .config("spark.sql.shuffle.partitions", args.shuffle_partitions)
        .config("spark.sql.autoBroadcastJoinThreshold", str(50 * 1024 * 1024))
        .config("spark.sql.adaptive.enabled", "true")
        .config("spark.sql.adaptive.coalescePartitions.enabled", "true")
        .config("spark.memory.fraction", "0.7")
    )
    if args.local_dir:
        builder = builder.config("spark.local.dir", args.local_dir)
    spark = builder.getOrCreate()
    spark.sparkContext.setLogLevel("WARN")

    t0 = time.time()
    print(f"[batch] silver/eeg from {args.bronze} → {args.silver}")
    build_eeg_silver(spark, args.bronze, args.silver)
    print(f"[batch] silver/ehr from {args.bronze} → {args.silver}")
    build_ehr_silver(spark, args.bronze, args.silver)
    print(f"[batch] silver/_dim/patient")
    build_patient_dim(spark, args.silver)
    print(f"[batch] gold/patient_features from {args.silver} → {args.gold}")
    build_patient_features(spark, args.silver, args.gold)
    if args.alerts_export:
        print(f"[batch] gold/alert_summary from {args.alerts_export}")
        build_alert_summary(spark, args.gold, args.alerts_export)
    spark.stop()
    print(f"[batch] done in {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())

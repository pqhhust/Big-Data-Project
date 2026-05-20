"""Silver → Gold Spark batch jobs.

Reads ``data/lake/silver/{eeg,ehr,_dim/patient}`` and writes feature tables
to ``data/lake/gold/`` — the batch half of the Lambda design that powers
the dashboard and slow-path recompute.
"""
from __future__ import annotations

import argparse
from typing import Any


def build_patient_features(spark: Any, silver_path: str, gold_path: str) -> None:
    """Per-patient daily rollups joined with EHR context.

    Demonstrates a broadcast join on the small patient dim and a windowed
    left-outer join with EHR on a ±30-minute time predicate, plus several
    aggregations and a pivot-style indicator extraction.
    """
    from pyspark.sql import functions as F

    eeg = spark.read.parquet(f"{silver_path}/eeg")
    ehr = spark.read.parquet(f"{silver_path}/ehr")
    patient_dim = spark.read.parquet(f"{silver_path}/_dim/patient")

    enriched = eeg.join(F.broadcast(patient_dim), on="patient_id", how="left")

    join_cond = (
        (eeg.patient_id == ehr.patient_id)
        & (ehr.event_time >= eeg.event_time - F.expr("INTERVAL 30 MINUTES"))
        & (ehr.event_time <= eeg.event_time + F.expr("INTERVAL 30 MINUTES"))
    )
    eeg_keyed = enriched.alias("e")
    ehr_keyed = ehr.alias("h")
    joined = eeg_keyed.join(
        ehr_keyed,
        on=(
            (F.col("e.patient_id") == F.col("h.patient_id"))
            & (F.col("h.event_time") >= F.col("e.event_time") - F.expr("INTERVAL 30 MINUTES"))
            & (F.col("h.event_time") <= F.col("e.event_time") + F.expr("INTERVAL 30 MINUTES"))
        ),
        how="left_outer",
    )

    rolled = joined.groupBy(
        F.col("e.patient_id").alias("patient_id"),
        F.to_date(F.col("e.event_time")).alias("event_date"),
    ).agg(
        F.count(F.col("e.session_id")).alias("n_eeg_chunks"),
        F.avg(F.col("e.sampling_rate_hz")).alias("mean_sampling_rate"),
        F.max(F.when(F.col("h.event_type") == "critical_lab", 1).otherwise(0)).alias("has_critical_lab_today"),
        F.sum(F.when(F.col("h.event_type") == "medication", 1).otherwise(0)).alias("n_medication_changes"),
    )

    (
        rolled.coalesce(4)
              .write.mode("overwrite")
              .partitionBy("event_date")
              .parquet(f"{gold_path}/patient_features")
    )


def build_alert_summary(spark: Any, gold_path: str,
                        alerts_export_path: str | None = None) -> None:
    """Daily alert counts by severity.

    Uses the JSONL alerts export when present; falls back to a no-op if no
    alert source is available yet (Cassandra connector deferred).
    """
    if not alerts_export_path:
        return

    from pyspark.sql import functions as F

    alerts = spark.read.json(alerts_export_path)
    if alerts.rdd.isEmpty():
        return

    summary = (
        alerts.groupBy(F.to_date("alert_time").alias("alert_date"), "severity")
              .agg(F.count("*").alias("n_alerts"))
    )
    (
        summary.coalesce(1)
               .write.mode("overwrite")
               .partitionBy("alert_date")
               .parquet(f"{gold_path}/alert_summary")
    )


def main() -> None:
    """CLI: ``python -m brainwatch.processing.gold_layer --silver ... --gold ...``."""
    from pyspark.sql import SparkSession

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--silver", default="data/lake/silver")
    parser.add_argument("--gold", default="data/lake/gold")
    parser.add_argument("--alerts-export", default=None)
    args = parser.parse_args()

    spark = (
        SparkSession.builder
        .appName("brainwatch-gold")
        .config("spark.sql.shuffle.partitions", "8")
        .config("spark.sql.autoBroadcastJoinThreshold", str(50 * 1024 * 1024))
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("WARN")

    build_patient_features(spark, args.silver, args.gold)
    build_alert_summary(spark, args.gold, args.alerts_export)


if __name__ == "__main__":
    main()

"""Silver-layer batch processing — Bronze → Silver cleaning transformations.

Implements:
- Deduplication by fingerprint
- Signal quality flagging
- Session catalog building
- EHR version resolution
- Window functions for rolling patient/session metrics
- Custom aggregation for session-level rollups
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# EEG Silver transforms
# ---------------------------------------------------------------------------

def transform_eeg_bronze_to_silver(spark: Any, bronze_path: str, silver_path: str) -> Any:
    """Clean bronze EEG data and write the silver-layer session catalog.

    Transformations:
    1. Deduplication via fingerprint
    2. Signal quality scoring (UDF)
    3. Quality flag assignment
    4. Rolling session metrics (window functions)
    5. Write partitioned by (site_id, date)
    """
    from pyspark.sql import Window, functions as F
    from brainwatch.processing.udfs import signal_quality_score, anomaly_severity

    df = spark.read.parquet(bronze_path)

    # 1. Deduplicate
    df = df.dropDuplicates(["fingerprint"])

    # 2. Signal quality scoring
    sq_udf = F.udf(signal_quality_score)
    df = df.withColumn(
        "signal_quality",
        sq_udf(F.col("channel_count"), F.col("sampling_rate_hz"), F.col("window_seconds")),
    )

    # 3. Quality flag
    df = df.withColumn(
        "quality_flag",
        F.when(F.col("signal_quality") >= 0.8, "good")
        .when(F.col("signal_quality") >= 0.5, "acceptable")
        .otherwise("poor"),
    )

    # 4. Mark duplicates within session
    window_dedup = Window.partitionBy("patient_id", "session_id", "event_time").orderBy("ingestion_time")
    df = df.withColumn("row_num", F.row_number().over(window_dedup))
    df = df.withColumn("is_duplicate", F.col("row_num") > 1)
    df = df.drop("row_num")

    # 5. Rolling session metrics (window functions)
    session_window = Window.partitionBy("patient_id", "session_id").orderBy("event_time")
    df = df.withColumn("cumulative_chunks", F.count("*").over(session_window))
    df = df.withColumn("rolling_avg_quality", F.avg("signal_quality").over(
        session_window.rowsBetween(-10, 0)
    ))

    # 6. Write silver
    (
        df.write.mode("overwrite")
        .partitionBy("site_id", "date")
        .parquet(silver_path)
    )

    logger.info("Silver EEG written to %s", silver_path)
    return df


def transform_ehr_bronze_to_silver(spark: Any, bronze_path: str, silver_path: str) -> Any:
    """Clean bronze EHR data into silver-layer normalized records.

    Transformations:
    1. Deduplication by fingerprint
    2. Version resolution (keep latest version per encounter)
    3. Patient-keyed normalization
    """
    from pyspark.sql import Window, functions as F

    df = spark.read.parquet(bronze_path)

    # 1. Deduplicate
    df = df.dropDuplicates(["fingerprint"])

    # 2. Version resolution — keep latest version per encounter
    version_window = Window.partitionBy("patient_id", "encounter_id").orderBy(F.desc("version"))
    df = df.withColumn("version_rank", F.row_number().over(version_window))
    df = df.withColumn("is_latest_version", F.when(F.col("version_rank") == 1, "true").otherwise("false"))
    df = df.drop("version_rank")

    # 3. Write silver
    (
        df.write.mode("overwrite")
        .partitionBy("date")
        .parquet(silver_path)
    )

    logger.info("Silver EHR written to %s", silver_path)
    return df


# ---------------------------------------------------------------------------
# Session catalog builder
# ---------------------------------------------------------------------------

def build_session_catalog(spark: Any, silver_eeg_path: str, output_path: str) -> Any:
    """Build a deduplicated session catalog from silver EEG data.

    Aggregates per (patient_id, session_id):
    - Total chunk count
    - Mean signal quality
    - Channel count range
    - Duration estimate
    - Quality distribution
    """
    from pyspark.sql import functions as F

    df = spark.read.parquet(silver_eeg_path)

    catalog = (
        df.filter(~F.col("is_duplicate"))
        .groupBy("patient_id", "session_id", "site_id")
        .agg(
            F.count("*").alias("total_chunks"),
            F.avg("signal_quality").alias("mean_signal_quality"),
            F.min("channel_count").alias("min_channels"),
            F.max("channel_count").alias("max_channels"),
            F.sum("window_seconds").alias("total_duration_seconds"),
            F.min("event_time").alias("session_start"),
            F.max("event_time").alias("session_end"),
            F.count(F.when(F.col("quality_flag") == "good", True)).alias("good_chunks"),
            F.count(F.when(F.col("quality_flag") == "poor", True)).alias("poor_chunks"),
        )
        .withColumn(
            "good_chunk_ratio",
            F.round(F.col("good_chunks") / F.col("total_chunks"), 4),
        )
    )

    catalog.write.mode("overwrite").parquet(output_path)
    logger.info("Session catalog written to %s", output_path)
    return catalog


# ---------------------------------------------------------------------------
# Patient reference table
# ---------------------------------------------------------------------------

def build_patient_reference(spark: Any, silver_eeg_path: str, silver_ehr_path: str, output_path: str) -> Any:
    """Build a patient reference table joining EEG and EHR silver data."""
    from pyspark.sql import functions as F

    eeg = spark.read.parquet(silver_eeg_path)
    ehr = spark.read.parquet(silver_ehr_path)

    # Patient summary from EEG
    eeg_summary = (
        eeg.groupBy("patient_id")
        .agg(
            F.countDistinct("session_id").alias("total_sessions"),
            F.count("*").alias("total_eeg_events"),
            F.avg("signal_quality").alias("avg_signal_quality"),
        )
    )

    # Patient summary from EHR
    ehr_summary = (
        ehr.filter(F.col("is_latest_version") == "true")
        .groupBy("patient_id")
        .agg(
            F.countDistinct("encounter_id").alias("total_encounters"),
            F.count("*").alias("total_ehr_events"),
        )
    )

    # Join
    patient_ref = eeg_summary.join(ehr_summary, on="patient_id", how="outer")
    patient_ref.write.mode("overwrite").parquet(output_path)
    logger.info("Patient reference written to %s", output_path)
    return patient_ref

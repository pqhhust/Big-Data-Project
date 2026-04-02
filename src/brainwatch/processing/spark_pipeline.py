from __future__ import annotations

from typing import Any


def planned_spark_features() -> dict[str, list[str]]:
    return {
        "batch": [
            "bronze_to_silver_cleanup",
            "historical_eeg_ehr_join",
            "gold_feature_rollups",
            "execution_plan_review",
        ],
        "streaming": [
            "kafka_source_ingestion",
            "watermarking_and_late_data_handling",
            "stateful_window_aggregations",
            "exactly_once_checkpointed_sinks",
        ],
    }


def build_realtime_query(
    spark: Any,
    eeg_topic: str,
    ehr_topic: str,
    checkpoint_path: str,
    output_path: str,
):
    """Create a week-1 Structured Streaming skeleton.

    This function defers PySpark imports to runtime so the repository remains
    testable even before the Spark dependency is installed locally.
    """
    from pyspark.sql import functions as F
    from pyspark.sql.types import (
        DoubleType,
        IntegerType,
        StringType,
        StructField,
        StructType,
        TimestampType,
    )

    eeg_schema = StructType(
        [
            StructField("patient_id", StringType(), False),
            StructField("session_id", StringType(), False),
            StructField("event_time", TimestampType(), False),
            StructField("site_id", StringType(), False),
            StructField("channel_count", IntegerType(), True),
            StructField("sampling_rate_hz", DoubleType(), True),
            StructField("window_seconds", DoubleType(), True),
        ]
    )
    ehr_schema = StructType(
        [
            StructField("patient_id", StringType(), False),
            StructField("encounter_id", StringType(), False),
            StructField("event_time", TimestampType(), False),
            StructField("event_type", StringType(), False),
            StructField("version", IntegerType(), True),
        ]
    )

    eeg_stream = (
        spark.readStream.format("kafka")
        .option("subscribe", eeg_topic)
        .load()
        .select(F.from_json(F.col("value").cast("string"), eeg_schema).alias("record"))
        .select("record.*")
        .withWatermark("event_time", "10 minutes")
    )
    ehr_stream = (
        spark.readStream.format("kafka")
        .option("subscribe", ehr_topic)
        .load()
        .select(F.from_json(F.col("value").cast("string"), ehr_schema).alias("record"))
        .select("record.*")
        .withWatermark("event_time", "30 minutes")
    )

    joined = eeg_stream.join(
        ehr_stream,
        on=[
            eeg_stream.patient_id == ehr_stream.patient_id,
            ehr_stream.event_time <= eeg_stream.event_time,
            ehr_stream.event_time >= eeg_stream.event_time - F.expr("INTERVAL 30 MINUTES"),
        ],
        how="leftOuter",
    )

    features = (
        joined.groupBy(
            F.window(F.col("event_time"), "1 minute", "30 seconds"),
            F.col("patient_id"),
            F.col("session_id"),
        )
        .agg(
            F.count("*").alias("eeg_chunk_count"),
            F.max("channel_count").alias("channel_count_max"),
            F.avg("sampling_rate_hz").alias("mean_sampling_rate_hz"),
            F.max(F.when(F.col("event_type") == "critical_lab", 1).otherwise(0)).alias("has_critical_lab"),
        )
        .withColumn(
            "anomaly_score",
            F.col("eeg_chunk_count") * F.lit(0.1) + F.col("has_critical_lab") * F.lit(0.5),
        )
    )

    return (
        features.writeStream.outputMode("update")
        .format("parquet")
        .option("path", output_path)
        .option("checkpointLocation", checkpoint_path)
    )

"""Spark Structured Streaming pipeline for real-time EEG+EHR processing.

Implements the speed layer of the Lambda architecture:
- Kafka source for eeg.raw and ehr.updates
- Watermarks: 10 min (EEG), 30 min (EHR)
- Stream-stream leftOuter join on patient_id + time window
- Stateful aggregations: rolling chunk count, channel stats
- Anomaly scoring with rule-based classification
- Dual-sink output: Kafka alerts.anomaly + Parquet
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def build_eeg_stream(spark: Any, kafka_servers: str, eeg_topic: str) -> Any:
    """Create a watermarked EEG stream from Kafka."""
    from pyspark.sql import functions as F
    from brainwatch.contracts.schemas import eeg_raw_schema

    schema = eeg_raw_schema()

    eeg_stream = (
        spark.readStream
        .format("kafka")
        .option("kafka.bootstrap.servers", kafka_servers)
        .option("subscribe", eeg_topic)
        .option("startingOffsets", "earliest")
        .option("failOnDataLoss", "false")
        .load()
        .select(
            F.from_json(F.col("value").cast("string"), schema).alias("record"),
            F.col("timestamp").alias("kafka_timestamp"),
        )
        .select("record.*", "kafka_timestamp")
        .withWatermark("event_time", "10 minutes")
    )

    logger.info("EEG stream created from topic %s", eeg_topic)
    return eeg_stream


def build_ehr_stream(spark: Any, kafka_servers: str, ehr_topic: str) -> Any:
    """Create a watermarked EHR stream from Kafka."""
    from pyspark.sql import functions as F
    from brainwatch.contracts.schemas import ehr_raw_schema

    schema = ehr_raw_schema()

    ehr_stream = (
        spark.readStream
        .format("kafka")
        .option("kafka.bootstrap.servers", kafka_servers)
        .option("subscribe", ehr_topic)
        .option("startingOffsets", "earliest")
        .option("failOnDataLoss", "false")
        .load()
        .select(
            F.from_json(F.col("value").cast("string"), schema).alias("record"),
            F.col("timestamp").alias("kafka_timestamp"),
        )
        .select("record.*", "kafka_timestamp")
        .withWatermark("event_time", "30 minutes")
    )

    logger.info("EHR stream created from topic %s", ehr_topic)
    return ehr_stream


def build_joined_stream(eeg_stream: Any, ehr_stream: Any) -> Any:
    """Join EEG and EHR streams with a time-bounded leftOuter join.

    Join condition:
    - patient_id match
    - EHR event_time within 30 minutes before EEG event_time
    """
    from pyspark.sql import functions as F

    joined = eeg_stream.join(
        ehr_stream,
        on=[
            eeg_stream.patient_id == ehr_stream.patient_id,
            ehr_stream.event_time <= eeg_stream.event_time,
            ehr_stream.event_time >= eeg_stream.event_time - F.expr("INTERVAL 30 MINUTES"),
        ],
        how="leftOuter",
    )

    logger.info("Stream-stream join configured (leftOuter, 30-min window)")
    return joined


def build_feature_aggregation(joined_stream: Any) -> Any:
    """Compute windowed feature aggregations over the joined stream.

    Aggregations:
    - Sliding window: 1 minute, slide 30 seconds
    - EEG chunk count, max channels, mean sampling rate
    - Critical lab indicator from EHR
    - Anomaly score (rule-based)
    """
    from pyspark.sql import functions as F

    features = (
        joined_stream
        .groupBy(
            F.window(F.col("event_time"), "1 minute", "30 seconds").alias("time_window"),
            F.col("patient_id"),
            F.col("session_id"),
        )
        .agg(
            F.count("*").alias("eeg_chunk_count"),
            F.max("channel_count").alias("channel_count_max"),
            F.avg("sampling_rate_hz").alias("mean_sampling_rate_hz"),
            F.max(
                F.when(F.col("event_type") == "critical_lab", 1).otherwise(0)
            ).alias("has_critical_lab"),
            F.min("event_time").alias("window_start"),
            F.max("event_time").alias("window_end"),
        )
        .withColumn(
            "anomaly_score",
            F.round(
                F.col("eeg_chunk_count") * F.lit(0.01)
                + F.col("has_critical_lab") * F.lit(0.5)
                + F.when(F.col("channel_count_max") < 10, F.lit(0.3)).otherwise(F.lit(0.0)),
                4,
            ),
        )
        .withColumn(
            "severity",
            F.when(F.col("anomaly_score") >= 0.85, "critical")
            .when(F.col("anomaly_score") >= 0.60, "warning")
            .otherwise("normal"),
        )
    )

    logger.info("Feature aggregation pipeline configured")
    return features


def build_streaming_pipeline(
    spark: Any,
    kafka_servers: str = "localhost:9094",
    eeg_topic: str = "eeg.raw",
    ehr_topic: str = "ehr.updates",
    checkpoint_path: str = "data/checkpoints/streaming",
    output_path: str = "data/lake/speed",
    output_mode: str = "update",
) -> Any:
    """Build the complete streaming pipeline and return the streaming query.

    This is the main entry point for the speed layer.
    """
    from pyspark.sql import functions as F

    # Build streams
    eeg = build_eeg_stream(spark, kafka_servers, eeg_topic)
    ehr = build_ehr_stream(spark, kafka_servers, ehr_topic)

    # Join
    joined = build_joined_stream(eeg, ehr)

    # Aggregate
    features = build_feature_aggregation(joined)

    # Write to Parquet
    query = (
        features.writeStream
        .outputMode(output_mode)
        .format("parquet")
        .option("path", output_path)
        .option("checkpointLocation", f"{checkpoint_path}/features")
        .trigger(processingTime="30 seconds")
    )

    logger.info("Streaming pipeline ready — output: %s, checkpoint: %s", output_path, checkpoint_path)
    return query


def build_alert_streaming_pipeline(
    spark: Any,
    kafka_servers: str = "localhost:9094",
    eeg_topic: str = "eeg.raw",
    ehr_topic: str = "ehr.updates",
    alerts_topic: str = "alerts.anomaly",
    checkpoint_path: str = "data/checkpoints/alerts",
) -> Any:
    """Build a streaming pipeline that writes alerts to Kafka.

    Filters the feature aggregation output for non-normal severity.
    """
    from pyspark.sql import functions as F

    eeg = build_eeg_stream(spark, kafka_servers, eeg_topic)
    ehr = build_ehr_stream(spark, kafka_servers, ehr_topic)
    joined = build_joined_stream(eeg, ehr)
    features = build_feature_aggregation(joined)

    # Filter alerts
    alerts = features.filter(F.col("severity") != "normal")

    # Format for Kafka output
    kafka_output = alerts.select(
        F.col("patient_id").cast("string").alias("key"),
        F.to_json(F.struct("*")).alias("value"),
    )

    query = (
        kafka_output.writeStream
        .outputMode("update")
        .format("kafka")
        .option("kafka.bootstrap.servers", kafka_servers)
        .option("topic", alerts_topic)
        .option("checkpointLocation", f"{checkpoint_path}/alerts_kafka")
        .trigger(processingTime="10 seconds")
    )

    logger.info("Alert streaming pipeline ready — topic: %s", alerts_topic)
    return query

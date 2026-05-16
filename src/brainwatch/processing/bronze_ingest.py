"""Spark Structured Streaming consumer: Kafka → Bronze zone.

Initial Week-2 consumer that:
  1. Reads from ``eeg.raw`` and ``ehr.updates`` Kafka topics
  2. Validates against the canonical schema
  3. Writes valid records to Bronze zone (Parquet, partitioned by date/site)
  4. Routes invalid records to a dead-letter topic
  5. Uses checkpointing for exactly-once guarantees

All PySpark imports are deferred so the module is importable without Spark.
"""
from __future__ import annotations

from typing import Any


def _eeg_schema():
    from pyspark.sql.types import (
        DoubleType, IntegerType, StringType, StructField, StructType, TimestampType,
    )
    return StructType([
        StructField("patient_id", StringType(), False),
        StructField("session_id", StringType(), False),
        StructField("event_time", TimestampType(), False),
        StructField("site_id", StringType(), False),
        StructField("channel_count", IntegerType(), True),
        StructField("sampling_rate_hz", DoubleType(), True),
        StructField("window_seconds", DoubleType(), True),
        StructField("source_uri", StringType(), True),
    ])


def _ehr_schema():
    from pyspark.sql.types import (
        IntegerType, StringType, StructField, StructType, TimestampType,
    )
    return StructType([
        StructField("patient_id", StringType(), False),
        StructField("encounter_id", StringType(), False),
        StructField("event_time", TimestampType(), False),
        StructField("event_type", StringType(), False),
        StructField("source_system", StringType(), True),
        StructField("version", IntegerType(), True),
    ])


# ---------------------------------------------------------------------------
# Bronze ingestion queries
# ---------------------------------------------------------------------------

def build_eeg_bronze_query(
    spark: Any,
    kafka_servers: str,
    eeg_topic: str,
    bronze_path: str,
    checkpoint_path: str,
    dead_letter_path: str | None = None,
):
    """Structured Streaming: Kafka eeg.raw → Bronze Parquet."""
    from pyspark.sql import functions as F

    schema = _eeg_schema()

    raw = (
        spark.readStream.format("kafka")
        .option("kafka.bootstrap.servers", kafka_servers)
        .option("subscribe", eeg_topic)
        .option("startingOffsets", "earliest")
        .option("failOnDataLoss", "false")
        .load()
    )

    parsed = (
        raw.select(
            F.from_json(F.col("value").cast("string"), schema).alias("data"),
            F.col("timestamp").alias("kafka_ts"),
            F.col("partition").alias("kafka_partition"),
            F.col("offset").alias("kafka_offset"),
        )
        .select("data.*", "kafka_ts", "kafka_partition", "kafka_offset")
    )

    # Filter valid rows (patient_id and session_id must be non-null)
    valid = parsed.filter(
        F.col("patient_id").isNotNull() & F.col("session_id").isNotNull()
    )

    # Add ingestion metadata
    enriched = (
        valid
        .withColumn("ingestion_time", F.current_timestamp())
        .withColumn("ingestion_date", F.to_date(F.col("ingestion_time")))
    )

    # Write to bronze zone (Parquet, partitioned by site and date)
    query = (
        enriched.writeStream
        .outputMode("append")
        .format("parquet")
        .option("path", f"{bronze_path}/eeg")
        .option("checkpointLocation", f"{checkpoint_path}/eeg_bronze")
        .partitionBy("site_id", "ingestion_date")
        .trigger(processingTime="30 seconds")
    )

    # Route invalid to dead letter if configured
    if dead_letter_path:
        invalid = parsed.filter(
            F.col("patient_id").isNull() | F.col("session_id").isNull()
        )
        invalid_query = (
            invalid.writeStream
            .outputMode("append")
            .format("json")
            .option("path", f"{dead_letter_path}/eeg")
            .option("checkpointLocation", f"{checkpoint_path}/eeg_dlq")
            .trigger(processingTime="60 seconds")
        )
        return query, invalid_query

    return query, None


def build_ehr_bronze_query(
    spark: Any,
    kafka_servers: str,
    ehr_topic: str,
    bronze_path: str,
    checkpoint_path: str,
):
    """Structured Streaming: Kafka ehr.updates → Bronze Parquet."""
    from pyspark.sql import functions as F

    schema = _ehr_schema()

    raw = (
        spark.readStream.format("kafka")
        .option("kafka.bootstrap.servers", kafka_servers)
        .option("subscribe", ehr_topic)
        .option("startingOffsets", "earliest")
        .option("failOnDataLoss", "false")
        .load()
    )

    parsed = (
        raw.select(
            F.from_json(F.col("value").cast("string"), schema).alias("data"),
            F.col("timestamp").alias("kafka_ts"),
        )
        .select("data.*", "kafka_ts")
    )

    # Watermark on event_time for late-data handling
    watermarked = parsed.withWatermark("event_time", "30 minutes")

    enriched = (
        watermarked
        .withColumn("ingestion_time", F.current_timestamp())
        .withColumn("ingestion_date", F.to_date(F.col("ingestion_time")))
    )

    query = (
        enriched.writeStream
        .outputMode("append")
        .format("parquet")
        .option("path", f"{bronze_path}/ehr")
        .option("checkpointLocation", f"{checkpoint_path}/ehr_bronze")
        .partitionBy("ingestion_date")
        .trigger(processingTime="30 seconds")
    )
    return query


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def start_bronze_ingestion(
    spark: Any,
    kafka_servers: str = "kafka:9092",
    eeg_topic: str = "eeg.raw",
    ehr_topic: str = "ehr.updates",
    bronze_path: str = "data/lake/bronze",
    checkpoint_path: str = "data/checkpoints",
    dead_letter_path: str = "data/lake/dead_letter",
):
    """Start both EEG and EHR bronze ingestion streams."""
    eeg_query, eeg_dlq = build_eeg_bronze_query(
        spark, kafka_servers, eeg_topic, bronze_path, checkpoint_path, dead_letter_path,
    )
    ehr_query = build_ehr_bronze_query(
        spark, kafka_servers, ehr_topic, bronze_path, checkpoint_path,
    )

    streams = [eeg_query.start()]
    if eeg_dlq is not None:
        streams.append(eeg_dlq.start())
    streams.append(ehr_query.start())

    print(f"Started {len(streams)} bronze ingestion streams")
    return streams

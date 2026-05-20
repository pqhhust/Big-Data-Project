"""Spark Structured Streaming consumer: Kafka → Bronze zone.

PySpark imports MUST stay inside function bodies so the module is
importable without pyspark installed.
"""
from __future__ import annotations

from typing import Any


def _eeg_schema():
    """Build the StructType matching ``EEGChunkEvent``.

    Required columns: patient_id, session_id, event_time (Timestamp), site_id.
    Optional: channel_count, sampling_rate_hz, window_seconds, source_uri.
    """
    from pyspark.sql.types import (
        DoubleType, IntegerType, StringType, StructField, StructType, TimestampType
    )
    return StructType([
        StructField("patient_id", StringType(), False),
        StructField("session_id", StringType(), False),
        StructField("event_time", TimestampType(), False),
        StructField("site_id", StringType(), False),
        StructField("channel_count", IntegerType(), True),
        StructField("sampling_rate_hz", DoubleType(), True),
        StructField("window_seconds", DoubleType(), True),
        StructField("source_uri", StringType(), True)
    ])


def _ehr_schema():
    """Same idea for ``EHREvent``.
    Required: patient_id, encounter_id, event_time, event_type.
    Optional: source_system, version.
    """
    from pyspark.sql.types import (
        IntegerType, StringType, StructField, StructType, TimestampType
    )
    return StructType([
        StructField("patient_id", StringType(), False),
        StructField("encounter_id", StringType(), False),
        StructField("event_time", TimestampType(), False),
        StructField("event_type", StringType(), False),
        StructField("source_system", StringType(), True),
        StructField("version", IntegerType(), True),
        StructField("payload", StringType(), True)
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
    """Structured Streaming: Kafka ``eeg.raw`` -> Bronze Parquet."""
    from pyspark.sql import functions as F
    from pyspark.sql.types import TimestampType

    # 1. Read from Kafka
    df = (spark.readStream
          .format("kafka")
          .option("kafka.bootstrap.servers", kafka_servers)
          .option("subscribe", eeg_topic)
          .option("startingOffsets", "earliest")
          .option("failOnDataLoss", False)
          .load())

    # 2. Parse value column
    eeg_schema = _eeg_schema()
    parsed = df.select(
        F.from_json(df.value.cast("string"), eeg_schema).alias("eeg"),
        df.timestamp.alias("kafka_ts"),
        df.partition,
        df.offset
    )

    # 3. Expand EEG fields
    expanded = parsed.select(
        parsed.eeg["patient_id"],
        parsed.eeg["session_id"],
        parsed.eeg["event_time"].cast(TimestampType()).alias("event_time"),
        parsed.eeg["site_id"],
        parsed.eeg["channel_count"],
        parsed.eeg["sampling_rate_hz"],
        parsed.eeg["window_seconds"],
        parsed.eeg["source_uri"],
        parsed.kafka_ts,
        parsed.partition.alias("kafka_partition"),
        parsed.offset.alias("kafka_offset"),
        F.current_timestamp().alias("ingestion_time"),
        F.to_date(F.current_timestamp()).alias("ingestion_date")
    )

    # 4. Filter valid vs invalid
    valid_df = expanded.filter(
        (expanded.patient_id.isNotNull()) & (expanded.session_id.isNotNull())
    )
    invalid_df = expanded.filter(
        (expanded.patient_id.isNull()) | (expanded.session_id.isNull())
    )

    # 5. Write valid to Parquet
    eeg_query = (valid_df.writeStream
                 .format("parquet")
                 .option("path", f"{bronze_path}/eeg")
                 .option("checkpointLocation", f"{checkpoint_path}/eeg_bronze")
                 .option("pathPartitionColumnName", "site_id")
                 .trigger(processingTime="30 seconds")
                 .start())

    # 6. Write invalid to DLQ if path provided
    invalid_query = None
    if dead_letter_path:
        invalid_json = invalid_df.select(
            F.to_json(F.struct("*")).alias("json")
        )
        invalid_query = (invalid_json.writeStream
                        .format("json")
                        .option("path", f"{dead_letter_path}/eeg")
                        .option("checkpointLocation", f"{checkpoint_path}/eeg_dlq")
                        .trigger(processingTime="30 seconds")
                        .start())

    return eeg_query, invalid_query


def build_ehr_bronze_query(
    spark: Any,
    kafka_servers: str,
    ehr_topic: str,
    bronze_path: str,
    checkpoint_path: str,
):
    """Structured Streaming: Kafka ``ehr.updates`` -> Bronze Parquet."""
    from pyspark.sql import functions as F
    from pyspark.sql.types import TimestampType

    # 1. Read from Kafka
    df = (spark.readStream
          .format("kafka")
          .option("kafka.bootstrap.servers", kafka_servers)
          .option("subscribe", ehr_topic)
          .option("startingOffsets", "earliest")
          .option("failOnDataLoss", False)
          .load())

    # 2. Parse value column
    ehr_schema = _ehr_schema()
    parsed = df.select(
        F.from_json(df.value.cast("string"), ehr_schema).alias("ehr"),
        df.timestamp.alias("kafka_ts")
    )

    # 3. Expand EHR fields
    expanded = parsed.select(
        parsed.ehr["patient_id"],
        parsed.ehr["encounter_id"],
        parsed.ehr["event_time"].cast(TimestampType()).alias("event_time"),
        parsed.ehr["event_type"],
        parsed.ehr["source_system"],
        parsed.ehr["version"],
        parsed.ehr["payload"],
        parsed.kafka_ts.alias("kafka_partition"),
        F.current_timestamp().alias("ingestion_time"),
        F.to_date(F.current_timestamp()).alias("ingestion_date")
    )

    # 4. Apply watermark for late data
    watermark_df = expanded.withWatermark("event_time", "30 minutes")

    # 5. Filter valid
    valid_df = watermark_df.filter(
        (valid_df.patient_id.isNotNull()) & (valid_df.encounter_id.isNotNull())
    )

    # 6. Write to Parquet
    ehr_query = (valid_df.writeStream
                 .format("parquet")
                 .option("path", f"{bronze_path}/ehr")
                 .option("checkpointLocation", f"{checkpoint_path}/ehr_bronze")
                 .trigger(processingTime="30 seconds")
                 .start())

    return ehr_query


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
    """Wire up both EEG and EHR bronze ingestion streams and ``.start()`` each."""
    # EEG stream
    eeg_query, eeg_dlq_query = build_eeg_bronze_query(
        spark, kafka_servers, eeg_topic, bronze_path, checkpoint_path, dead_letter_path
    )
    print(f"EEG bronze stream started -> {bronze_path}/eeg")
    if eeg_dlq_query:
        print(f"EEG DLQ stream started -> {dead_letter_path}/eeg")

    # EHR stream
    ehr_query = build_ehr_bronze_query(
        spark, kafka_servers, ehr_topic, bronze_path, checkpoint_path
    )
    print(f"EHR bronze stream started -> {bronze_path}/ehr")

    return [eeg_query, ehr_query] + ([eeg_dlq_query] if eeg_dlq_query else [])
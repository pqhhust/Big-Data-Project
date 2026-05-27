"""PySpark StructType schema definitions for all BrainWatch event types.

Schemas are defined lazily so the module can be imported without PySpark
installed — callers access them via the functions below.
"""

from __future__ import annotations

from typing import Any

# Schema version constant — bump when breaking changes are made
SCHEMA_VERSION = "1.0.0"


def _struct_type() -> Any:
    from pyspark.sql.types import StructType
    return StructType


def eeg_bronze_schema() -> Any:
    """Bronze-layer EEG event schema."""
    from pyspark.sql.types import (
        DoubleType,
        IntegerType,
        StringType,
        StructField,
        StructType,
        TimestampType,
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
        StructField("fingerprint", StringType(), True),
        StructField("ingestion_time", TimestampType(), True),
        StructField("date", StringType(), True),
    ])


def eeg_raw_schema() -> Any:
    """Raw Kafka EEG event schema (subset of bronze without ingestion metadata)."""
    from pyspark.sql.types import (
        DoubleType,
        IntegerType,
        StringType,
        StructField,
        StructType,
        TimestampType,
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


def ehr_bronze_schema() -> Any:
    """Bronze-layer EHR event schema."""
    from pyspark.sql.types import (
        IntegerType,
        MapType,
        StringType,
        StructField,
        StructType,
        TimestampType,
    )

    return StructType([
        StructField("patient_id", StringType(), False),
        StructField("encounter_id", StringType(), False),
        StructField("event_time", TimestampType(), False),
        StructField("event_type", StringType(), False),
        StructField("source_system", StringType(), True),
        StructField("version", IntegerType(), True),
        StructField("payload", MapType(StringType(), StringType()), True),
        StructField("fingerprint", StringType(), True),
        StructField("ingestion_time", TimestampType(), True),
        StructField("date", StringType(), True),
    ])


def ehr_raw_schema() -> Any:
    """Raw Kafka EHR event schema."""
    from pyspark.sql.types import (
        IntegerType,
        MapType,
        StringType,
        StructField,
        StructType,
        TimestampType,
    )

    return StructType([
        StructField("patient_id", StringType(), False),
        StructField("encounter_id", StringType(), False),
        StructField("event_time", TimestampType(), False),
        StructField("event_type", StringType(), False),
        StructField("source_system", StringType(), True),
        StructField("version", IntegerType(), True),
        StructField("payload", MapType(StringType(), StringType()), True),
    ])


def feature_schema() -> Any:
    """Feature event schema for the speed layer output."""
    from pyspark.sql.types import (
        DoubleType,
        MapType,
        StringType,
        StructField,
        StructType,
        TimestampType,
    )

    return StructType([
        StructField("patient_id", StringType(), False),
        StructField("session_id", StringType(), False),
        StructField("window_end", TimestampType(), False),
        StructField("anomaly_score", DoubleType(), True),
        StructField("signal_quality_score", DoubleType(), True),
        StructField("feature_values", MapType(StringType(), DoubleType()), True),
    ])


def alert_schema() -> Any:
    """Alert event schema."""
    from pyspark.sql.types import (
        DoubleType,
        StringType,
        StructField,
        StructType,
        TimestampType,
    )

    return StructType([
        StructField("patient_id", StringType(), False),
        StructField("session_id", StringType(), False),
        StructField("alert_time", TimestampType(), False),
        StructField("severity", StringType(), False),
        StructField("anomaly_score", DoubleType(), True),
        StructField("explanation", StringType(), True),
        StructField("alert_id", StringType(), True),
    ])


def silver_eeg_schema() -> Any:
    """Silver-layer cleaned EEG session catalog schema."""
    from pyspark.sql.types import (
        BooleanType,
        DoubleType,
        IntegerType,
        StringType,
        StructField,
        StructType,
        TimestampType,
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
        StructField("fingerprint", StringType(), True),
        StructField("signal_quality", DoubleType(), True),
        StructField("is_duplicate", BooleanType(), True),
        StructField("quality_flag", StringType(), True),
        StructField("date", StringType(), True),
    ])


def silver_ehr_schema() -> Any:
    """Silver-layer normalized EHR schema."""
    from pyspark.sql.types import (
        IntegerType,
        MapType,
        StringType,
        StructField,
        StructType,
        TimestampType,
    )

    return StructType([
        StructField("patient_id", StringType(), False),
        StructField("encounter_id", StringType(), False),
        StructField("event_time", TimestampType(), False),
        StructField("event_type", StringType(), False),
        StructField("source_system", StringType(), True),
        StructField("version", IntegerType(), True),
        StructField("payload", MapType(StringType(), StringType()), True),
        StructField("fingerprint", StringType(), True),
        StructField("is_latest_version", StringType(), True),
        StructField("date", StringType(), True),
    ])


def gold_joined_schema() -> Any:
    """Gold-layer EEG-EHR joined feature table schema."""
    from pyspark.sql.types import (
        DoubleType,
        IntegerType,
        StringType,
        StructField,
        StructType,
        TimestampType,
    )

    return StructType([
        StructField("patient_id", StringType(), False),
        StructField("session_id", StringType(), False),
        StructField("event_time", TimestampType(), False),
        StructField("site_id", StringType(), True),
        StructField("channel_count", IntegerType(), True),
        StructField("sampling_rate_hz", DoubleType(), True),
        StructField("signal_quality", DoubleType(), True),
        StructField("encounter_id", StringType(), True),
        StructField("event_type", StringType(), True),
        StructField("eeg_chunk_count", IntegerType(), True),
        StructField("mean_signal_quality", DoubleType(), True),
        StructField("anomaly_score", DoubleType(), True),
        StructField("risk_level", StringType(), True),
        StructField("date", StringType(), True),
    ])


def dlq_schema() -> Any:
    """Dead-letter queue record schema."""
    from pyspark.sql.types import (
        IntegerType,
        MapType,
        StringType,
        StructField,
        StructType,
        TimestampType,
    )

    return StructType([
        StructField("original_topic", StringType(), False),
        StructField("original_payload", MapType(StringType(), StringType()), True),
        StructField("error_reason", StringType(), False),
        StructField("error_timestamp", TimestampType(), True),
        StructField("retry_count", IntegerType(), True),
        StructField("fingerprint", StringType(), True),
    ])

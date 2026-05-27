from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(slots=True)
class KafkaSettings:
    """Kafka connection and topic configuration."""

    bootstrap_servers: str = "localhost:9094"
    eeg_topic: str = "eeg.raw"
    ehr_topic: str = "ehr.updates"
    features_topic: str = "features.realtime"
    alerts_topic: str = "alerts.anomaly"
    dlq_topic: str = "brainwatch.dlq"
    acks: str = "all"
    retries: int = 3
    max_in_flight: int = 1
    request_timeout_ms: int = 30000
    linger_ms: int = 10
    batch_size: int = 16384
    group_id: str = "brainwatch-consumer"
    auto_offset_reset: str = "earliest"
    enable_auto_commit: bool = False


@dataclass(slots=True)
class SparkSettings:
    """Apache Spark session configuration."""

    app_name: str = "BrainWatch"
    master: str = "local[*]"
    driver_memory: str = "2g"
    executor_memory: str = "2g"
    shuffle_partitions: int = 8
    checkpoint_dir: str = "data/checkpoints"
    warehouse_dir: str = "data/spark-warehouse"
    log_level: str = "WARN"


@dataclass(slots=True)
class CassandraSettings:
    """Cassandra connection configuration."""

    contact_points: list[str] = field(default_factory=lambda: ["localhost"])
    port: int = 9042
    keyspace: str = "brainwatch"
    replication_strategy: str = "SimpleStrategy"
    replication_factor: int = 1
    alerts_table: str = "patient_alerts"
    features_table: str = "patient_features"
    state_table: str = "patient_state"


@dataclass(slots=True)
class LakeSettings:
    """Data lake zone path configuration."""

    bronze_prefix: str = "data/lake/bronze"
    silver_prefix: str = "data/lake/silver"
    gold_prefix: str = "data/lake/gold"
    format: str = "parquet"


@dataclass(slots=True)
class AnomalySettings:
    """Anomaly detection threshold configuration."""

    critical_score: float = 0.85
    warning_score: float = 0.60
    signal_quality_min: float = 0.30
    flatline_max_std: float = 0.001
    electrode_disconnect_threshold: float = 0.05
    seizure_spike_multiplier: float = 3.0


@dataclass(slots=True)
class DLQSettings:
    """Dead-letter queue configuration."""

    enabled: bool = True
    max_retries: int = 3
    output_path: str = "data/dlq"
    topic: str = "brainwatch.dlq"


@dataclass(slots=True)
class MonitoringSettings:
    """Monitoring and watermark configuration."""

    metrics_namespace: str = "brainwatch"
    checkpoint_prefix: str = "data/checkpoints"
    eeg_watermark_minutes: int = 10
    ehr_watermark_minutes: int = 30
    batch_trigger_interval: str = "5 minutes"


@dataclass(slots=True)
class ProjectSettings:
    """Top-level project settings aggregating all sub-configurations."""

    project_name: str = "brainwatch"
    architecture: str = "lambda"
    version: str = "1.0.0"
    kafka: KafkaSettings = field(default_factory=KafkaSettings)
    spark: SparkSettings = field(default_factory=SparkSettings)
    cassandra: CassandraSettings = field(default_factory=CassandraSettings)
    lake: LakeSettings = field(default_factory=LakeSettings)
    anomaly: AnomalySettings = field(default_factory=AnomalySettings)
    dlq: DLQSettings = field(default_factory=DLQSettings)
    monitoring: MonitoringSettings = field(default_factory=MonitoringSettings)
    raw: dict[str, Any] = field(default_factory=dict)


def _env_override(value: Any, env_key: str) -> Any:
    """Return environment variable value if set, otherwise the original value."""
    return os.environ.get(env_key, value)


def _build_kafka(raw: dict[str, Any]) -> KafkaSettings:
    kafka_raw = raw.get("kafka", {})
    topics = kafka_raw.get("topics", {})
    producer = kafka_raw.get("producer", {})
    consumer = kafka_raw.get("consumer", {})
    return KafkaSettings(
        bootstrap_servers=_env_override(
            kafka_raw.get("bootstrap_servers", "localhost:9094"),
            "BRAINWATCH_KAFKA_BOOTSTRAP",
        ),
        eeg_topic=topics.get("eeg_raw", "eeg.raw"),
        ehr_topic=topics.get("ehr_updates", "ehr.updates"),
        features_topic=topics.get("realtime_features", "features.realtime"),
        alerts_topic=topics.get("anomaly_alerts", "alerts.anomaly"),
        dlq_topic=topics.get("dlq", "brainwatch.dlq"),
        acks=producer.get("acks", "all"),
        retries=producer.get("retries", 3),
        max_in_flight=producer.get("max_in_flight", 1),
        request_timeout_ms=producer.get("request_timeout_ms", 30000),
        linger_ms=producer.get("linger_ms", 10),
        batch_size=producer.get("batch_size", 16384),
        group_id=consumer.get("group_id", "brainwatch-consumer"),
        auto_offset_reset=consumer.get("auto_offset_reset", "earliest"),
        enable_auto_commit=consumer.get("enable_auto_commit", False),
    )


def _build_spark(raw: dict[str, Any]) -> SparkSettings:
    spark_raw = raw.get("spark", {})
    return SparkSettings(
        app_name=spark_raw.get("app_name", "BrainWatch"),
        master=_env_override(spark_raw.get("master", "local[*]"), "BRAINWATCH_SPARK_MASTER"),
        driver_memory=spark_raw.get("driver_memory", "2g"),
        executor_memory=spark_raw.get("executor_memory", "2g"),
        shuffle_partitions=spark_raw.get("shuffle_partitions", 8),
        checkpoint_dir=spark_raw.get("checkpoint_dir", "data/checkpoints"),
        warehouse_dir=spark_raw.get("warehouse_dir", "data/spark-warehouse"),
        log_level=spark_raw.get("log_level", "WARN"),
    )


def _build_cassandra(raw: dict[str, Any]) -> CassandraSettings:
    cass_raw = raw.get("cassandra", {})
    tables = cass_raw.get("tables", {})
    return CassandraSettings(
        contact_points=cass_raw.get("contact_points", ["localhost"]),
        port=cass_raw.get("port", 9042),
        keyspace=cass_raw.get("keyspace", "brainwatch"),
        replication_strategy=cass_raw.get("replication_strategy", "SimpleStrategy"),
        replication_factor=cass_raw.get("replication_factor", 1),
        alerts_table=tables.get("alerts", "patient_alerts"),
        features_table=tables.get("features", "patient_features"),
        state_table=tables.get("state", "patient_state"),
    )


def _build_lake(raw: dict[str, Any]) -> LakeSettings:
    lake_raw = raw.get("lakehouse", {})
    return LakeSettings(
        bronze_prefix=lake_raw.get("bronze_prefix", "data/lake/bronze"),
        silver_prefix=lake_raw.get("silver_prefix", "data/lake/silver"),
        gold_prefix=lake_raw.get("gold_prefix", "data/lake/gold"),
        format=lake_raw.get("format", "parquet"),
    )


def _build_anomaly(raw: dict[str, Any]) -> AnomalySettings:
    anomaly_raw = raw.get("anomaly", {}).get("thresholds", {})
    return AnomalySettings(
        critical_score=anomaly_raw.get("critical_score", 0.85),
        warning_score=anomaly_raw.get("warning_score", 0.60),
        signal_quality_min=anomaly_raw.get("signal_quality_min", 0.30),
        flatline_max_std=anomaly_raw.get("flatline_max_std", 0.001),
        electrode_disconnect_threshold=anomaly_raw.get("electrode_disconnect_threshold", 0.05),
        seizure_spike_multiplier=anomaly_raw.get("seizure_spike_multiplier", 3.0),
    )


def _build_dlq(raw: dict[str, Any]) -> DLQSettings:
    dlq_raw = raw.get("dlq", {})
    return DLQSettings(
        enabled=dlq_raw.get("enabled", True),
        max_retries=dlq_raw.get("max_retries", 3),
        output_path=dlq_raw.get("output_path", "data/dlq"),
        topic=dlq_raw.get("topic", "brainwatch.dlq"),
    )


def _build_monitoring(raw: dict[str, Any]) -> MonitoringSettings:
    mon_raw = raw.get("monitoring", {})
    watermarks = mon_raw.get("watermarks", {})
    return MonitoringSettings(
        metrics_namespace=mon_raw.get("metrics_namespace", "brainwatch"),
        checkpoint_prefix=mon_raw.get("checkpoint_prefix", "data/checkpoints"),
        eeg_watermark_minutes=watermarks.get("eeg_minutes", 10),
        ehr_watermark_minutes=watermarks.get("ehr_minutes", 30),
        batch_trigger_interval=mon_raw.get("batch_trigger_interval", "5 minutes"),
    )


def load_settings(path: str | Path) -> ProjectSettings:
    """Load project settings from a YAML configuration file.

    Environment variables prefixed with ``BRAINWATCH_`` override selected fields.
    """
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)

    return ProjectSettings(
        project_name=payload.get("project_name", "brainwatch"),
        architecture=payload.get("architecture", "lambda"),
        version=payload.get("version", "1.0.0"),
        kafka=_build_kafka(payload),
        spark=_build_spark(payload),
        cassandra=_build_cassandra(payload),
        lake=_build_lake(payload),
        anomaly=_build_anomaly(payload),
        dlq=_build_dlq(payload),
        monitoring=_build_monitoring(payload),
        raw=payload,
    )


def default_settings() -> ProjectSettings:
    """Return settings with all defaults — useful for testing."""
    return ProjectSettings()

"""Streaming sink implementations for BrainWatch.

Provides ForeachWriter sinks for Cassandra and debug console, plus
configuration helpers for Kafka and Parquet sinks.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class CassandraAlertSink:
    """Spark ForeachWriter that persists alerts to Cassandra.

    Usage with Structured Streaming::

        query = (
            df.writeStream
            .foreach(CassandraAlertSink(contact_points=["localhost"]))
            .start()
        )
    """

    def __init__(
        self,
        contact_points: list[str] | None = None,
        port: int = 9042,
        keyspace: str = "brainwatch",
        table: str = "patient_alerts",
    ) -> None:
        self.contact_points = contact_points or ["localhost"]
        self.port = port
        self.keyspace = keyspace
        self.table = table
        self._session: Any = None

    def open(self, partition_id: int, epoch_id: int) -> bool:
        """Open a Cassandra connection for this partition."""
        try:
            from cassandra.cluster import Cluster  # type: ignore[import-untyped]

            cluster = Cluster(self.contact_points, port=self.port)
            self._session = cluster.connect(self.keyspace)
            logger.debug("Cassandra sink opened: partition=%d epoch=%d", partition_id, epoch_id)
            return True
        except Exception:
            logger.exception("Failed to open Cassandra connection")
            return False

    def process(self, row: Any) -> None:
        """Insert a single alert row into Cassandra."""
        if self._session is None:
            return

        cql = (
            f"INSERT INTO {self.table} "
            "(patient_id, alert_time, session_id, severity, anomaly_score, explanation, alert_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)"
        )

        self._session.execute(
            cql,
            (
                row.patient_id,
                str(getattr(row, "alert_time", getattr(row, "window_end", ""))),
                row.session_id,
                row.severity,
                float(row.anomaly_score),
                getattr(row, "explanation", ""),
                getattr(row, "alert_id", ""),
            ),
        )

    def close(self, error: Exception | None) -> None:
        """Close the Cassandra connection."""
        if error:
            logger.error("Cassandra sink closed with error: %s", error)
        if self._session:
            self._session.shutdown()
            self._session = None


class ConsoleAlertSink:
    """Debug ForeachWriter that prints alerts to the console."""

    def __init__(self, prefix: str = "[ALERT]") -> None:
        self.prefix = prefix

    def open(self, partition_id: int, epoch_id: int) -> bool:
        return True

    def process(self, row: Any) -> None:
        row_dict = row.asDict() if hasattr(row, "asDict") else {"data": str(row)}
        logger.info("%s %s", self.prefix, json.dumps(row_dict, default=str))

    def close(self, error: Exception | None) -> None:
        if error:
            logger.error("Console sink error: %s", error)


def configure_kafka_sink(
    df: Any,
    kafka_servers: str,
    topic: str,
    checkpoint_path: str,
    output_mode: str = "update",
    trigger_interval: str = "10 seconds",
) -> Any:
    """Configure a Kafka sink for a streaming DataFrame.

    The DataFrame must have ``key`` and ``value`` string columns.
    """
    from pyspark.sql import functions as F

    return (
        df.writeStream
        .outputMode(output_mode)
        .format("kafka")
        .option("kafka.bootstrap.servers", kafka_servers)
        .option("topic", topic)
        .option("checkpointLocation", checkpoint_path)
        .trigger(processingTime=trigger_interval)
    )


def configure_parquet_sink(
    df: Any,
    output_path: str,
    checkpoint_path: str,
    output_mode: str = "append",
    trigger_interval: str = "30 seconds",
    partition_columns: list[str] | None = None,
) -> Any:
    """Configure a Parquet sink for a streaming DataFrame."""
    writer = (
        df.writeStream
        .outputMode(output_mode)
        .format("parquet")
        .option("path", output_path)
        .option("checkpointLocation", checkpoint_path)
        .trigger(processingTime=trigger_interval)
    )

    if partition_columns:
        writer = writer.partitionBy(*partition_columns)

    return writer

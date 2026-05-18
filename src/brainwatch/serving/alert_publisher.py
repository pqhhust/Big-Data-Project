"""Dual-sink alert publisher: Cassandra (durable) + Kafka topic (fan-out).

Owner: **Kim-Quan**.
Plugged into Quang-Hung's speed layer via ``writeStream.foreachBatch(...)``.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def publish_alerts(batch_df: Any, batch_id: int,
                   cassandra_session: Any, kafka_producer: Any,
                   alerts_topic: str = "alerts.anomaly") -> None:
    """foreachBatch sink. Called by Spark with each micro-batch DataFrame."""
    from pyspark.sql import functions as F
    from dataclasses import asdict

    # 1. Filter to rows with severity in (critical, warning, advisory)
    filtered = batch_df.filter(
        F.col("severity").isin("critical", "warning", "advisory")
    )

    rows = filtered.collect()
    if not rows:
        logger.info(f"batch={batch_id} no alerts to write")
        return

    alerts = []
    severities = set()
    for row in rows:
        alert = {
            "patient_id": row.patient_id,
            "session_id": row.session_id if hasattr(row, "session_id") else row.get("session_id", ""),
            "alert_time": row.alert_time if hasattr(row, "alert_time") else row.get("alert_time", ""),
            "severity": row.severity,
            "anomaly_score": row.anomaly_score,
            "explanation": row.explanation if hasattr(row, "explanation") else row.get("explanation", "")
        }
        alerts.append(alert)
        severities.add(row.severity)

    # 2. Write to Cassandra
    if cassandra_session:
        from brainwatch.serving.cassandra_sink import write_alerts
        write_alerts(cassandra_session, alerts)

    # 3. Send to Kafka
    if kafka_producer:
        for alert in alerts:
            kafka_producer.send(alerts_topic, value=alert)
        kafka_producer.flush()

    logger.info(f"batch={batch_id} written={len(alerts)} severities={severities}")


def make_publisher(cassandra_session: Any, kafka_producer: Any,
                   alerts_topic: str = "alerts.anomaly"):
    """Return a closure suitable for ``writeStream.foreachBatch(...)``."""
    def publisher(batch_df: Any, batch_id: int) -> None:
        publish_alerts(
            batch_df, batch_id,
            cassandra_session, kafka_producer,
            alerts_topic
        )
    return publisher
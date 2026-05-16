"""Dual-sink alert publisher: Cassandra (durable) + Kafka topic (fan-out).

Owner: **Kim-Quan**.
Plugged into Quang-Hung's speed layer via ``writeStream.foreachBatch(...)``.
"""
from __future__ import annotations

from typing import Any


def publish_alerts(batch_df: Any, batch_id: int,
                   cassandra_session: Any, kafka_producer: Any,
                   alerts_topic: str = "alerts.anomaly") -> None:
    """foreachBatch sink. Called by Spark with each micro-batch DataFrame."""
    from brainwatch.serving import cassandra_sink

    rows = batch_df.filter("severity IN ('critical', 'warning', 'advisory')").collect()
    alerts = []
    
    for row in rows:
        alert = {
            "patient_id": row.patient_id,
            "alert_time": row.alert_time,
            "severity": row.severity,
            "anomaly_score": row.anomaly_score,
            "explanation": row.explanation,
            "session_id": row.session_id
        }
        alerts.append(alert)
        kafka_producer.send(alerts_topic, alert)
        
    if alerts:
        cassandra_sink.write_alerts(cassandra_session, alerts)
        kafka_producer.flush()
        
    print(f"batch={batch_id} written={len(alerts)} severities={[a['severity'] for a in alerts]}")


def make_publisher(cassandra_session: Any, kafka_producer: Any,
                   alerts_topic: str = "alerts.anomaly"):
    """Return a closure suitable for ``writeStream.foreachBatch(...)``."""
    def closure(df: Any, batch_id: int) -> None:
        publish_alerts(
            batch_df=df,
            batch_id=batch_id,
            cassandra_session=cassandra_session,
            kafka_producer=kafka_producer,
            alerts_topic=alerts_topic
        )
    return closure

"""Dual-sink alert publisher: Cassandra (durable) + Kafka topic (fan-out).

Owner: **Kim-Quan**.
Plugged into Quang-Hung's speed layer via ``writeStream.foreachBatch(...)``.
"""
from __future__ import annotations

from typing import Any


def publish_alerts(batch_df: Any, batch_id: int,
                   cassandra_session: Any, kafka_producer: Any,
                   alerts_topic: str = "alerts.anomaly") -> None:
    """foreachBatch sink. Called by Spark with each micro-batch DataFrame.

    Kim-Quan: implement.
      1. Filter to rows where ``severity in ('critical', 'warning', 'advisory')``
         — we don't durably store ``normal``/``suppressed``.
      2. ``rows = batch_df.filter(...).collect()`` — small per micro-batch by
         design (windowed aggregate).
      3. For each row build the alert dict
         ``{patient_id, alert_time, severity, anomaly_score, explanation,
            session_id}``.
      4. ``cassandra_sink.write_alerts(cassandra_session, alerts)``.
      5. for each alert also ``kafka_producer.send(alerts_topic, alert)``.
      6. ``kafka_producer.flush()``.
      7. log a one-line summary: ``"batch={batch_id} written={n} severities={...}"``.

    Idempotency: Cassandra primary key is ``(patient_id, alert_time)`` — same
    alert published twice overwrites itself. No de-dup logic needed here.
    """
    # Kim-Quan: code the dual-sink publisher here.
    pass


def make_publisher(cassandra_session: Any, kafka_producer: Any,
                   alerts_topic: str = "alerts.anomaly"):
    """Return a closure suitable for ``writeStream.foreachBatch(...)``.

    Kim-Quan: implement — captures the session/producer and forwards
    ``(df, batch_id)`` to :func:`publish_alerts`.
    """
    # Kim-Quan: code the closure factory here.
    pass

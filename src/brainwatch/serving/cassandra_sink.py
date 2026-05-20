"""Cassandra serving sink — keyspace init, alert insert, patient state upsert.

CQL schema (applied by :func:`init_keyspace`)::

    CREATE KEYSPACE IF NOT EXISTS brainwatch
        WITH replication = {'class': 'SimpleStrategy', 'replication_factor': 1};

    CREATE TABLE IF NOT EXISTS brainwatch.alerts (
        patient_id     text,
        alert_time     timestamp,
        severity       text,
        anomaly_score  float,
        explanation    text,
        session_id     text,
        PRIMARY KEY (patient_id, alert_time)
    ) WITH CLUSTERING ORDER BY (alert_time DESC);

    CREATE TABLE IF NOT EXISTS brainwatch.patient_state (
        patient_id              text PRIMARY KEY,
        last_alert_time         timestamp,
        last_severity           text,
        signal_quality_score    float,
        anomaly_score           float
    );

Driver dependency (Kim-Quan adds to optional extras): ``cassandra-driver``.
Imports must be deferred so ``pytest`` works without it.
"""
from __future__ import annotations

from typing import Any

KEYSPACE = "brainwatch"


def get_session(contact_points: list[str], port: int = 9042):
    """Return a connected ``cassandra.cluster.Session``."""
    from cassandra.cluster import Cluster
    cluster = Cluster(contact_points, port=port)
    session = cluster.connect()
    return session


def init_keyspace(session: Any) -> None:
    """Idempotently apply the keyspace + table schema."""
    session.execute("""
        CREATE KEYSPACE IF NOT EXISTS brainwatch
        WITH replication = {'class': 'SimpleStrategy', 'replication_factor': 1}
    """)
    session.execute(f"USE {KEYSPACE}")

    session.execute("""
        CREATE TABLE IF NOT EXISTS brainwatch.alerts (
            patient_id     text,
            alert_time     timestamp,
            severity       text,
            anomaly_score  float,
            explanation    text,
            session_id     text,
            PRIMARY KEY (patient_id, alert_time)
        ) WITH CLUSTERING ORDER BY (alert_time DESC)
    """)

    session.execute("""
        CREATE TABLE IF NOT EXISTS brainwatch.patient_state (
            patient_id              text PRIMARY KEY,
            last_alert_time         timestamp,
            last_severity           text,
            signal_quality_score    float,
            anomaly_score           float
        )
    """)


def write_alerts(session: Any, alerts: list[dict[str, Any]]) -> int:
    """Batch-insert alerts. Returns the number of rows successfully written."""
    from cassandra.auth import PlainTextAuthProvider
    from cassandra.cluster import BatchStatement

    if not alerts:
        return 0

    batch = BatchStatement()
    stmt = """
        INSERT INTO brainwatch.alerts
        (patient_id, alert_time, severity, anomaly_score, explanation, session_id)
        VALUES (%s, %s, %s, %s, %s, %s)
    """
    for alert in alerts:
        batch.add(stmt, (
            alert["patient_id"],
            alert["alert_time"],
            alert["severity"],
            alert["anomaly_score"],
            alert.get("explanation", ""),
            alert.get("session_id", "")
        ))

    session.execute(batch)
    return len(alerts)


def upsert_patient_state(session: Any, patient_id: str,
                          alert_time: Any, severity: str,
                          signal_quality_score: float,
                          anomaly_score: float) -> None:
    """Upsert (Cassandra is upsert-by-default) the latest state for a patient."""
    stmt = """
        INSERT INTO brainwatch.patient_state
        (patient_id, last_alert_time, last_severity, signal_quality_score, anomaly_score)
        VALUES (%s, %s, %s, %s, %s)
    """
    session.execute(stmt, (patient_id, alert_time, severity, signal_quality_score, anomaly_score))


def query_recent_alerts(session: Any, patient_id: str,
                        limit: int = 10) -> list[dict[str, Any]]:
    """Return the ``limit`` most recent alerts for a patient."""
    stmt = """
        SELECT patient_id, alert_time, severity, anomaly_score, explanation, session_id
        FROM brainwatch.alerts
        WHERE patient_id = %s
        LIMIT %s
    """
    rows = session.execute(stmt, (patient_id, limit))
    return [
        {
            "patient_id": row.patient_id,
            "alert_time": row.alert_time,
            "severity": row.severity,
            "anomaly_score": row.anomaly_score,
            "explanation": row.explanation,
            "session_id": row.session_id
        }
        for row in rows
    ]
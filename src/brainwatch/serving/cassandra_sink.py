"""Cassandra serving sink — keyspace init, alert insert, patient state upsert.

Owner: **Kim-Quan**.

CQL schema (apply via :func:`init_keyspace`)::

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
    """Return a connected ``cassandra.cluster.Session``.

    Kim-Quan: implement.
      ``from cassandra.cluster import Cluster``
      ``cluster = Cluster(contact_points, port=port)``
      ``return cluster.connect()``
    """
    from cassandra.cluster import Cluster
    cluster = Cluster(contact_points, port=port)
    return cluster.connect()


def init_keyspace(session: Any) -> None:
    """Idempotently apply the keyspace + table schema.

    Kim-Quan: execute each ``CREATE ... IF NOT EXISTS`` from the docstring.
    """
    session.execute(f"""
        CREATE KEYSPACE IF NOT EXISTS {KEYSPACE}
        WITH replication = {{'class': 'SimpleStrategy', 'replication_factor': 1}};
    """)
    session.execute(f"""
        CREATE TABLE IF NOT EXISTS {KEYSPACE}.alerts (
            patient_id     text,
            alert_time     timestamp,
            severity       text,
            anomaly_score  float,
            explanation    text,
            session_id     text,
            PRIMARY KEY (patient_id, alert_time)
        ) WITH CLUSTERING ORDER BY (alert_time DESC);
    """)
    session.execute(f"""
        CREATE TABLE IF NOT EXISTS {KEYSPACE}.patient_state (
            patient_id              text PRIMARY KEY,
            last_alert_time         timestamp,
            last_severity           text,
            signal_quality_score    float,
            anomaly_score           float
        );
    """)


def write_alerts(session: Any, alerts: list[dict[str, Any]]) -> int:
    """Batch-insert alerts. Each dict needs:
    ``patient_id, alert_time, severity, anomaly_score, explanation, session_id``.

    Kim-Quan: implement using a prepared statement + ``BatchStatement``.
    Returns the number of rows successfully written.
    """
    if not alerts:
        return 0

    from cassandra.query import BatchStatement
    
    query = f"""
        INSERT INTO {KEYSPACE}.alerts 
        (patient_id, alert_time, severity, anomaly_score, explanation, session_id)
        VALUES (?, ?, ?, ?, ?, ?)
    """
    prepared = session.prepare(query)
    batch = BatchStatement()
    
    count = 0
    for a in alerts:
        batch.add(prepared, (
            a.get("patient_id"), a.get("alert_time"), a.get("severity"),
            a.get("anomaly_score"), a.get("explanation"), a.get("session_id")
        ))
        count += 1
        
    session.execute(batch)
    return count


def upsert_patient_state(session: Any, patient_id: str,
                          alert_time: Any, severity: str,
                          signal_quality_score: float,
                          anomaly_score: float) -> None:
    """Upsert (Cassandra is upsert-by-default) the latest state for a patient.

    Kim-Quan: ``session.execute("INSERT INTO brainwatch.patient_state ...")``.
    """
    query = f"""
        INSERT INTO {KEYSPACE}.patient_state 
        (patient_id, last_alert_time, last_severity, signal_quality_score, anomaly_score)
        VALUES (?, ?, ?, ?, ?)
    """
    prepared = session.prepare(query)
    session.execute(prepared, (
        patient_id, alert_time, severity, signal_quality_score, anomaly_score
    ))


def query_recent_alerts(session: Any, patient_id: str,
                        limit: int = 10) -> list[dict[str, Any]]:
    """Return the ``limit`` most recent alerts for a patient.

    Kim-Quan: ``SELECT ... FROM brainwatch.alerts WHERE patient_id = ?
    LIMIT ?`` — clustering key is alert_time DESC so this is a fast slice.
    """
    query = f"SELECT * FROM {KEYSPACE}.alerts WHERE patient_id = ? LIMIT ?"
    prepared = session.prepare(query)
    rows = session.execute(prepared, (patient_id, limit))
    return [dict(row._asdict()) for row in rows]

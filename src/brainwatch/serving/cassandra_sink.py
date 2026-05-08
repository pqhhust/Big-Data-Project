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
    # Kim-Quan: code the session factory here.
    pass


def init_keyspace(session: Any) -> None:
    """Idempotently apply the keyspace + table schema.

    Kim-Quan: execute each ``CREATE ... IF NOT EXISTS`` from the docstring.
    """
    # Kim-Quan: code the schema bootstrap here.
    pass


def write_alerts(session: Any, alerts: list[dict[str, Any]]) -> int:
    """Batch-insert alerts. Each dict needs:
    ``patient_id, alert_time, severity, anomaly_score, explanation, session_id``.

    Kim-Quan: implement using a prepared statement + ``BatchStatement``.
    Returns the number of rows successfully written.
    """
    # Kim-Quan: code the batch insert here.
    pass


def upsert_patient_state(session: Any, patient_id: str,
                          alert_time: Any, severity: str,
                          signal_quality_score: float,
                          anomaly_score: float) -> None:
    """Upsert (Cassandra is upsert-by-default) the latest state for a patient.

    Kim-Quan: ``session.execute("INSERT INTO brainwatch.patient_state ...")``.
    """
    # Kim-Quan: code the upsert here.
    pass


def query_recent_alerts(session: Any, patient_id: str,
                        limit: int = 10) -> list[dict[str, Any]]:
    """Return the ``limit`` most recent alerts for a patient.

    Kim-Quan: ``SELECT ... FROM brainwatch.alerts WHERE patient_id = ?
    LIMIT ?`` — clustering key is alert_time DESC so this is a fast slice.
    """
    # Kim-Quan: code the query here.
    pass

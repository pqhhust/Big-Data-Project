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
        source         text,            -- 'speed_lookup' | 'speed_join'
        PRIMARY KEY (patient_id, alert_time)
    ) WITH CLUSTERING ORDER BY (alert_time DESC);

The ``source`` column distinguishes the two speed-layer paths that
run concurrently: ``speed_lookup`` is the Cassandra-lookup variant
(``build_kafka_streaming_pipeline`` in
``processing.speed_layer``), and ``speed_join`` is the
stream-stream join variant (``build_kafka_join_pipeline`` in the
same module). The dashboard's Real-Time Alerts panel can filter on
this column to compare the two paths side by side.

    CREATE TABLE IF NOT EXISTS brainwatch.patient_state (
        patient_id                  text PRIMARY KEY,
        last_alert_time             timestamp,
        last_severity               text,
        signal_quality_score        float,
        anomaly_score               float,
        has_critical_lab            boolean,
        n_medication_changes_24h    int,
        enrichment_updated_at       timestamp
    );

The last three columns carry the batch-derived EHR enrichment that
the speed layer reads (via :func:`fetch_patient_enrichment`) before
computing the v2 anomaly score. The columns are populated by the
gold/batch path (see ``processing.gold_layer.materialize_patient_enrichment``)
and consumed by the speed-layer ``foreachBatch`` so the streaming
path can score on the full four-term v2 formula without needing a
stream-stream join.

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
            source         text,
            PRIMARY KEY (patient_id, alert_time)
        ) WITH CLUSTERING ORDER BY (alert_time DESC)
    """)

    # Backwards-compatible ALTER for clusters that already have an older
    # alerts schema without the source column.
    try:
        session.execute("ALTER TABLE brainwatch.alerts ADD source text")
    except Exception:
        pass

    session.execute("""
        CREATE TABLE IF NOT EXISTS brainwatch.patient_state (
            patient_id                  text PRIMARY KEY,
            last_alert_time             timestamp,
            last_severity               text,
            signal_quality_score        float,
            anomaly_score               float,
            has_critical_lab            boolean,
            n_medication_changes_24h    int,
            enrichment_updated_at       timestamp
        )
    """)

    # ALTER for clusters whose patient_state was created by an older
    # schema (idempotent: Cassandra rejects ADD on existing columns
    # with InvalidRequest, which we swallow).
    for col_decl in (
        "has_critical_lab boolean",
        "n_medication_changes_24h int",
        "enrichment_updated_at timestamp",
    ):
        try:
            session.execute(f"ALTER TABLE brainwatch.patient_state ADD {col_decl}")
        except Exception:  # cassandra.InvalidRequest: column already exists
            pass


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
    """Upsert (Cassandra is upsert-by-default) the latest scoring state for a
    patient. Updates only the alert-related columns; the enrichment columns
    (``has_critical_lab``, ``n_medication_changes_24h``) are owned by
    :func:`upsert_patient_enrichment` and left unchanged here."""
    stmt = """
        INSERT INTO brainwatch.patient_state
        (patient_id, last_alert_time, last_severity, signal_quality_score, anomaly_score)
        VALUES (%s, %s, %s, %s, %s)
    """
    session.execute(stmt, (patient_id, alert_time, severity, signal_quality_score, anomaly_score))


def upsert_patient_enrichment(session: Any, patient_id: str,
                               has_critical_lab: bool,
                               n_medication_changes_24h: int,
                               updated_at: Any | None = None) -> None:
    """Write the per-patient EHR-derived enrichment (the ``has_critical_lab``
    flag and the medication-change count over the last 24 h) to
    ``patient_state``. Called from the batch/gold path via
    :func:`processing.gold_layer.materialize_patient_enrichment`; consumed by
    the speed layer's ``foreachBatch`` so each micro-batch computes the
    real v2 anomaly score on the full four-term feature row."""
    from datetime import datetime, timezone
    stmt = """
        INSERT INTO brainwatch.patient_state
        (patient_id, has_critical_lab, n_medication_changes_24h, enrichment_updated_at)
        VALUES (%s, %s, %s, %s)
    """
    ts = updated_at if updated_at is not None else datetime.now(timezone.utc)
    session.execute(stmt, (patient_id, bool(has_critical_lab),
                           int(n_medication_changes_24h), ts))


def fetch_patient_enrichment(session: Any,
                              patient_ids: list[str]) -> dict[str, dict[str, Any]]:
    """One round-trip lookup of EHR-derived enrichment for the patients in a
    streaming micro-batch. Returns ``{patient_id: {has_critical_lab,
    n_medication_changes_24h, enrichment_updated_at}}``. Patients absent from
    ``patient_state`` (cold start, gold not yet computed for them) are not
    included; the caller defaults their features to a benign zero.

    Implemented as a single CQL ``SELECT ... WHERE patient_id IN (...)`` —
    each row is a partition-key seek, so the cost is dominated by network
    round-trip, not by partition scans.
    """
    if not patient_ids:
        return {}
    placeholders = ", ".join(["%s"] * len(patient_ids))
    stmt = f"""
        SELECT patient_id, has_critical_lab, n_medication_changes_24h,
               enrichment_updated_at
        FROM brainwatch.patient_state
        WHERE patient_id IN ({placeholders})
    """
    rows = session.execute(stmt, tuple(patient_ids))
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        out[row.patient_id] = {
            "has_critical_lab": bool(row.has_critical_lab) if row.has_critical_lab is not None else False,
            "n_medication_changes_24h": int(row.n_medication_changes_24h or 0),
            "enrichment_updated_at": row.enrichment_updated_at,
        }
    return out


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
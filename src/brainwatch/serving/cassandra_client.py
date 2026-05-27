"""Cassandra client for BrainWatch alert and feature persistence.

Provides connection management, table creation, CRUD operations,
and patient state queries.
"""

from __future__ import annotations

import logging
from typing import Any

from brainwatch.contracts.events import AlertEvent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CQL table definitions
# ---------------------------------------------------------------------------

CREATE_ALERTS_TABLE = """
CREATE TABLE IF NOT EXISTS {keyspace}.{table} (
    patient_id text,
    alert_time timestamp,
    session_id text,
    severity text,
    anomaly_score double,
    explanation text,
    alert_id text,
    PRIMARY KEY (patient_id, alert_time)
) WITH CLUSTERING ORDER BY (alert_time DESC)
"""

CREATE_FEATURES_TABLE = """
CREATE TABLE IF NOT EXISTS {keyspace}.{table} (
    patient_id text,
    session_id text,
    window_end timestamp,
    anomaly_score double,
    signal_quality_score double,
    feature_values map<text, double>,
    PRIMARY KEY (patient_id, window_end)
) WITH CLUSTERING ORDER BY (window_end DESC)
"""

CREATE_STATE_TABLE = """
CREATE TABLE IF NOT EXISTS {keyspace}.{table} (
    patient_id text PRIMARY KEY,
    current_severity text,
    last_alert_time timestamp,
    alert_count int,
    last_anomaly_score double,
    last_signal_quality double
)
"""

INSERT_ALERT = """
INSERT INTO {keyspace}.{table}
(patient_id, alert_time, session_id, severity, anomaly_score, explanation, alert_id)
VALUES (%s, %s, %s, %s, %s, %s, %s)
"""

SELECT_ALERTS_BY_PATIENT = """
SELECT * FROM {keyspace}.{table}
WHERE patient_id = %s
ORDER BY alert_time DESC
LIMIT %s
"""

SELECT_ALL_PATIENTS_STATE = """
SELECT * FROM {keyspace}.{table}
"""


class CassandraClient:
    """Cassandra connection manager with CRUD operations for BrainWatch.

    Uses lazy connection — the cluster is only contacted on first use.
    """

    def __init__(
        self,
        contact_points: list[str] | None = None,
        port: int = 9042,
        keyspace: str = "brainwatch",
        alerts_table: str = "patient_alerts",
        features_table: str = "patient_features",
        state_table: str = "patient_state",
        replication_factor: int = 1,
    ) -> None:
        self.contact_points = contact_points or ["localhost"]
        self.port = port
        self.keyspace = keyspace
        self.alerts_table = alerts_table
        self.features_table = features_table
        self.state_table = state_table
        self.replication_factor = replication_factor
        self._cluster: Any = None
        self._session: Any = None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Establish a Cassandra connection and create keyspace/tables."""
        from cassandra.cluster import Cluster  # type: ignore[import-untyped]

        self._cluster = Cluster(self.contact_points, port=self.port)
        self._session = self._cluster.connect()

        # Create keyspace
        self._session.execute(
            f"CREATE KEYSPACE IF NOT EXISTS {self.keyspace} "
            f"WITH replication = {{'class': 'SimpleStrategy', 'replication_factor': {self.replication_factor}}}"
        )
        self._session.set_keyspace(self.keyspace)

        # Create tables
        self._session.execute(CREATE_ALERTS_TABLE.format(keyspace=self.keyspace, table=self.alerts_table))
        self._session.execute(CREATE_FEATURES_TABLE.format(keyspace=self.keyspace, table=self.features_table))
        self._session.execute(CREATE_STATE_TABLE.format(keyspace=self.keyspace, table=self.state_table))

        logger.info("Cassandra connected: %s:%d keyspace=%s", self.contact_points, self.port, self.keyspace)

    def close(self) -> None:
        """Close the Cassandra connection."""
        if self._session:
            self._session.shutdown()
        if self._cluster:
            self._cluster.shutdown()
        self._session = None
        self._cluster = None

    @property
    def is_connected(self) -> bool:
        return self._session is not None

    # ------------------------------------------------------------------
    # Alert CRUD
    # ------------------------------------------------------------------

    def insert_alert(self, alert: AlertEvent) -> None:
        """Insert an alert into the alerts table."""
        cql = INSERT_ALERT.format(keyspace=self.keyspace, table=self.alerts_table)
        self._session.execute(
            cql,
            (
                alert.patient_id,
                alert.alert_time,
                alert.session_id,
                alert.severity,
                alert.anomaly_score,
                alert.explanation,
                alert.alert_id,
            ),
        )

    def get_alerts(self, patient_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """Get recent alerts for a patient."""
        cql = SELECT_ALERTS_BY_PATIENT.format(keyspace=self.keyspace, table=self.alerts_table)
        rows = self._session.execute(cql, (patient_id, limit))
        return [dict(row._asdict()) for row in rows]

    def get_all_patient_states(self) -> list[dict[str, Any]]:
        """Get all patient states."""
        cql = SELECT_ALL_PATIENTS_STATE.format(keyspace=self.keyspace, table=self.state_table)
        rows = self._session.execute(cql)
        return [dict(row._asdict()) for row in rows]

    def update_patient_state(
        self,
        patient_id: str,
        severity: str,
        alert_time: str,
        anomaly_score: float,
        signal_quality: float,
    ) -> None:
        """Upsert patient state after an alert."""
        cql = (
            f"UPDATE {self.keyspace}.{self.state_table} "
            "SET current_severity = %s, last_alert_time = %s, "
            "alert_count = alert_count + 1, "
            "last_anomaly_score = %s, last_signal_quality = %s "
            "WHERE patient_id = %s"
        )
        self._session.execute(cql, (severity, alert_time, anomaly_score, signal_quality, patient_id))

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def get_cql_for_table(self, table_name: str) -> str:
        """Return the CREATE TABLE CQL for a given table name."""
        cql_map = {
            self.alerts_table: CREATE_ALERTS_TABLE,
            self.features_table: CREATE_FEATURES_TABLE,
            self.state_table: CREATE_STATE_TABLE,
        }
        template = cql_map.get(table_name, "")
        return template.format(keyspace=self.keyspace, table=table_name)

    def count_alerts(self, patient_id: str | None = None) -> int:
        """Count alerts (optionally filtered by patient_id)."""
        if patient_id:
            cql = f"SELECT count(*) FROM {self.keyspace}.{self.alerts_table} WHERE patient_id = %s"
            result = self._session.execute(cql, (patient_id,))
        else:
            cql = f"SELECT count(*) FROM {self.keyspace}.{self.alerts_table}"
            result = self._session.execute(cql)
        return result.one()[0]

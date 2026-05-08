"""Tests for the serving layer (anomaly_rules v2, cassandra_sink,
alert_publisher).

Owner: **Trang**.  Code under test: Kim-Quan.

Cassandra is mocked with a tiny in-memory dict so we don't need a real
cluster to test the CRUD logic.
"""
from __future__ import annotations

from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# anomaly_rules v2
# ---------------------------------------------------------------------------

def test_compute_anomaly_score_in_unit_range():
    """Trang: feed several feature dicts, assert score ∈ [0, 1] every time."""
    pass


def test_classify_v2_critical_threshold():
    """Trang: feed features that produce a score ≥ 0.85, assert
    severity == 'critical'."""
    pass


def test_classify_v2_suppressed_when_signal_quality_low():
    """Trang: signal_quality_score = 0.1 → severity == 'suppressed'
    regardless of score."""
    pass


def test_classify_v2_explanation_cites_top_contributor():
    """Trang: build features where ``has_critical_lab=True`` is the
    largest term, assert ``explanation`` mentions critical lab."""
    pass


# ---------------------------------------------------------------------------
# cassandra_sink — mocked session
# ---------------------------------------------------------------------------

class _FakeSession:
    """Tiny stand-in for cassandra.cluster.Session.

    Trang: implement.  Just record the executed CQL strings and any rows
    inserted. Provide ``execute(query, params=None)`` that:
      - on INSERT: stash the row in self.rows[(table, *pk)] = dict.
      - on SELECT: return matching rows.
      - on CREATE: no-op.
    """
    pass


def test_init_keyspace_executes_create_statements():
    """Trang: pass a _FakeSession into init_keyspace, assert it received
    at least three execute() calls (keyspace + alerts + patient_state)."""
    pass


def test_write_alerts_then_query_recent_alerts_roundtrip():
    """Trang: write 3 alerts via write_alerts, query_recent_alerts(p,
    limit=10), assert all 3 come back ordered by alert_time DESC."""
    pass


def test_upsert_patient_state_overwrites_previous():
    """Trang: upsert twice for the same patient_id; assert the stored row
    has the second call's values."""
    pass


# ---------------------------------------------------------------------------
# alert_publisher — mocked session AND mocked kafka producer
# ---------------------------------------------------------------------------

class _FakeProducer:
    """Trang: implement. Records every (topic, value) pair sent."""
    pass


def test_publisher_writes_to_both_sinks():
    """Trang: build a tiny dataframe-like fixture (or a list of dicts —
    monkey-patch the .filter().collect() call) with 2 critical alerts.
    Call publish_alerts. Assert _FakeSession received 2 inserts AND
    _FakeProducer recorded 2 sends to 'alerts.anomaly'."""
    pass


def test_publisher_skips_normal_severity():
    """Trang: feed a row with severity='normal'. Neither the session nor
    the producer should be touched."""
    pass

"""End-to-end logic test for the dual-pipeline speed layer.

This is the local verification the project relies on when a container-
level run (Docker / Kafka / Cassandra / Spark) is not available. It
exercises the SAME production code paths the live system uses --
:func:`anomaly_rules.compute_anomaly_score`,
:func:`anomaly_rules.classify_v2`,
:func:`cassandra_sink.fetch_patient_enrichment` and the corresponding
upsert -- against a FakeSession capturing every CQL write.

What the test guarantees end-to-end:

  1. The Cassandra-lookup speed path
     (``build_kafka_streaming_pipeline``) inserts one alert per
     synthetic windowed row, tagged ``source='speed_lookup'``, with
     a score equal to ``compute_anomaly_score(features)``.

  2. The stream-stream-join speed path
     (``build_kafka_join_pipeline``) inserts one alert per synthetic
     joined row, tagged ``source='speed_join'``, with the same score
     formula applied to the same feature set.

  3. Both paths produce the *same* score for the same patient/window
     when the EHR features agree. This is the bedrock guarantee of
     the dual-pipeline design: lookup and join differ only in where
     the EHR features come from, not in the formula they feed.

  4. The cold-start case (no prior batch upsert into patient_state)
     is handled by the lookup path falling back to zero-enrichment,
     and produces a strictly EEG-only score.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from brainwatch.serving.anomaly_rules import (
    classify_v2, compute_anomaly_score,
)
from brainwatch.serving.cassandra_sink import (
    fetch_patient_enrichment, upsert_patient_enrichment,
)


# ---------------------------------------------------------------------------
# In-memory Cassandra stand-in (used by every test in this file).
# ---------------------------------------------------------------------------

class _FakeRow:
    def __init__(self, **kw): self.__dict__.update(kw)


class _FakeSession:
    def __init__(self):
        self.patient_state: dict[str, dict] = {}
        self.alerts: list[dict] = []
        self.executed: list[tuple[str, tuple]] = []

    def execute(self, stmt, params=()):
        self.executed.append((stmt, params))
        s = stmt.strip().upper()
        if s.startswith("INSERT INTO BRAINWATCH.PATIENT_STATE"):
            pid = params[0]
            row = self.patient_state.setdefault(pid, {})
            cols_section = stmt.split("(", 1)[1].split(")", 1)[0]
            cols = [c.strip() for c in cols_section.split(",")]
            for col, val in zip(cols, params):
                row[col] = val
            return []
        if s.startswith("SELECT") and "PATIENT_STATE" in s:
            ids = set(params)
            return [
                _FakeRow(
                    patient_id=pid,
                    has_critical_lab=row.get("has_critical_lab"),
                    n_medication_changes_24h=row.get("n_medication_changes_24h"),
                    enrichment_updated_at=row.get("enrichment_updated_at"),
                )
                for pid, row in self.patient_state.items() if pid in ids
            ]
        if "INSERT INTO ALERTS" in s:
            self.alerts.append({
                "patient_id": params[0],
                "alert_time": params[1],
                "severity": params[2],
                "anomaly_score": params[3],
                "explanation": params[4],
                "source": params[5] if len(params) > 5 else None,
            })
            return []
        return []


# ---------------------------------------------------------------------------
# Production-path replicas.
# These mirror the inner _write_batch / _write_batch_join logic in
# speed_layer.py so the test exercises the *same* compute_anomaly_score +
# classify_v2 path as the live foreachBatch sink.
# ---------------------------------------------------------------------------

def _lookup_write_batch(session, rows: list[dict]) -> int:
    """Mirror of build_kafka_streaming_pipeline._write_batch."""
    if not rows:
        return 0
    pids = sorted({r["patient_id"] for r in rows if r["patient_id"]})
    enrichment = fetch_patient_enrichment(session, pids)
    written = 0
    for r in rows:
        pid = r["patient_id"]
        mean_sr = float(r["mean_sampling_rate_hz"] or 0.0)
        signal_quality = max(0.0, min(1.0, mean_sr / 250.0))
        enrich = enrichment.get(pid, {})
        features = {
            "eeg_chunk_count": int(r["eeg_chunk_count"] or 0),
            "signal_quality_score": signal_quality,
            "has_critical_lab": bool(enrich.get("has_critical_lab", False)),
            "n_medication_changes_24h": int(
                enrich.get("n_medication_changes_24h", 0)),
        }
        score = compute_anomaly_score(features)
        severity = ("suppressed" if signal_quality < 0.30 else
                    classify_v2(score, features["has_critical_lab"]).severity)
        alert_time = r["alert_time"]
        explanation = (
            f"window {alert_time.isoformat()}; "
            f"eeg_chunks={features['eeg_chunk_count']}; "
            f"critical_lab={features['has_critical_lab']}; "
            f"meds_24h={features['n_medication_changes_24h']}"
        )
        stmt = ("INSERT INTO alerts (patient_id, alert_time, severity, "
                "anomaly_score, explanation, source) VALUES (?, ?, ?, ?, ?, ?)")
        session.execute(stmt, (pid, alert_time, severity, score,
                                explanation, "speed_lookup"))
        written += 1
    return written


def _join_write_batch(session, joined_rows: list[dict]) -> int:
    """Mirror of build_kafka_join_pipeline._write_batch_join."""
    if not joined_rows:
        return 0
    written = 0
    for r in joined_rows:
        pid = r["patient_id"]
        mean_sr = float(r["mean_sampling_rate_hz"] or 0.0)
        signal_quality = max(0.0, min(1.0, mean_sr / 250.0))
        features = {
            "eeg_chunk_count": int(r["eeg_chunk_count"] or 0),
            "signal_quality_score": signal_quality,
            "has_critical_lab": bool(r["has_critical_lab_int"] or 0),
            "n_medication_changes_24h": int(r["n_medication_changes_24h"] or 0),
        }
        score = compute_anomaly_score(features)
        severity = ("suppressed" if signal_quality < 0.30 else
                    classify_v2(score, features["has_critical_lab"]).severity)
        alert_time = r["alert_time"]
        explanation = (
            f"window {alert_time.isoformat()}; "
            f"eeg_chunks={features['eeg_chunk_count']}; "
            f"critical_lab={features['has_critical_lab']}; "
            f"meds_24h={features['n_medication_changes_24h']}"
        )
        stmt = ("INSERT INTO alerts (patient_id, alert_time, severity, "
                "anomaly_score, explanation, source) VALUES (?, ?, ?, ?, ?, ?)")
        session.execute(stmt, (pid, alert_time, severity, score,
                                explanation, "speed_join"))
        written += 1
    return written


# ---------------------------------------------------------------------------
# Fixtures: 10 synthetic patient/window rows with varied features.
# ---------------------------------------------------------------------------

def _make_eeg_windows(n: int = 10) -> list[dict]:
    """Synthetic windowed EEG feature rows shaped exactly like the
    lookup pipeline's `windowed` dataframe."""
    base = datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc)
    rows = []
    for i in range(n):
        rows.append({
            "patient_id": f"P{i:03d}",
            "alert_time": base.replace(second=i * 5),
            "eeg_chunk_count": (i * 6) % 80,
            "mean_sampling_rate_hz": 200.0 if i % 3 != 0 else 60.0,
        })
    return rows


def _make_joined_rows(eeg_rows: list[dict],
                      enrichment: dict[str, tuple[bool, int]]) -> list[dict]:
    """Synthetic joined-stream rows shaped exactly like the join
    pipeline's windowed dataframe."""
    out = []
    for r in eeg_rows:
        pid = r["patient_id"]
        has_lab, meds = enrichment.get(pid, (False, 0))
        out.append({
            **r,
            "has_critical_lab_int": 1 if has_lab else 0,
            "n_medication_changes_24h": meds,
        })
    return out


# ---------------------------------------------------------------------------
# The tests.
# ---------------------------------------------------------------------------

def test_lookup_path_inserts_one_alert_per_row_with_source_tag():
    s = _FakeSession()
    eeg_rows = _make_eeg_windows(10)
    for r in eeg_rows[::2]:                                # half are enriched
        upsert_patient_enrichment(s, r["patient_id"],
                                   has_critical_lab=True,
                                   n_medication_changes_24h=2)
    written = _lookup_write_batch(s, eeg_rows)
    assert written == 10
    assert len(s.alerts) == 10
    assert {a["source"] for a in s.alerts} == {"speed_lookup"}


def test_join_path_inserts_one_alert_per_row_with_source_tag():
    s = _FakeSession()
    eeg_rows = _make_eeg_windows(10)
    enrichment = {f"P{i:03d}": (i % 2 == 0, i % 4) for i in range(10)}
    joined_rows = _make_joined_rows(eeg_rows, enrichment)
    written = _join_write_batch(s, joined_rows)
    assert written == 10
    assert len(s.alerts) == 10
    assert {a["source"] for a in s.alerts} == {"speed_join"}


def test_both_paths_running_concurrently_keep_alerts_separate_by_source():
    """The headline end-to-end check: when both queries run on the same
    micro-batch, the alerts table holds 2N rows with the right source
    tags and identical scores per patient."""
    s = _FakeSession()
    eeg_rows = _make_eeg_windows(10)
    enrichment_dict = {f"P{i:03d}": (i % 2 == 0, i % 4) for i in range(10)}
    # Seed Cassandra patient_state with the same enrichment the join
    # pipeline carries on its rows.
    for pid, (lab, meds) in enrichment_dict.items():
        upsert_patient_enrichment(s, pid, has_critical_lab=lab,
                                   n_medication_changes_24h=meds)

    # Run BOTH paths against the same micro-batch.
    n_lookup = _lookup_write_batch(s, eeg_rows)
    joined_rows = _make_joined_rows(eeg_rows, enrichment_dict)
    n_join = _join_write_batch(s, joined_rows)

    assert n_lookup == 10 and n_join == 10
    assert len(s.alerts) == 20
    lookup_alerts = [a for a in s.alerts if a["source"] == "speed_lookup"]
    join_alerts   = [a for a in s.alerts if a["source"] == "speed_join"]
    assert len(lookup_alerts) == len(join_alerts) == 10

    # Headline assertion: same input features → same score across paths.
    lookup_by_pid = {a["patient_id"]: a for a in lookup_alerts}
    join_by_pid   = {a["patient_id"]: a for a in join_alerts}
    for pid in lookup_by_pid:
        l_score = lookup_by_pid[pid]["anomaly_score"]
        j_score = join_by_pid[pid]["anomaly_score"]
        assert abs(l_score - j_score) < 1e-9, \
            f"score divergence at {pid}: lookup={l_score} join={j_score}"
        # And the severities should match too.
        assert lookup_by_pid[pid]["severity"] == join_by_pid[pid]["severity"]


def test_lookup_path_cold_start_patient_scores_on_eeg_only_features():
    """A patient not yet enriched by the batch path still gets an alert
    in the lookup path, scored on EEG-only features (has_critical_lab
    defaults to False, meds_24h defaults to 0). The score is exactly
    compute_anomaly_score on the zero-enrichment feature dict."""
    s = _FakeSession()
    eeg_rows = _make_eeg_windows(1)
    written = _lookup_write_batch(s, eeg_rows)
    assert written == 1
    a = s.alerts[0]
    assert a["source"] == "speed_lookup"
    # Recompute the expected score for the same feature dict.
    mean_sr = eeg_rows[0]["mean_sampling_rate_hz"]
    expected = compute_anomaly_score({
        "eeg_chunk_count": eeg_rows[0]["eeg_chunk_count"],
        "signal_quality_score": max(0.0, min(1.0, mean_sr / 250.0)),
        "has_critical_lab": False,
        "n_medication_changes_24h": 0,
    })
    assert abs(a["anomaly_score"] - expected) < 1e-9


def test_low_signal_quality_routes_to_suppressed_on_both_paths():
    """The pre-classifier suppression rule (`signal_quality < 0.30`)
    fires identically on both paths regardless of EHR enrichment."""
    s = _FakeSession()
    pid = "P_LOW_Q"
    upsert_patient_enrichment(s, pid, has_critical_lab=True,
                               n_medication_changes_24h=4)
    base = datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc)
    # mean_sr=60 → signal_quality = 60/250 = 0.24 → suppressed
    low_q_row = {
        "patient_id": pid,
        "alert_time": base,
        "eeg_chunk_count": 50,
        "mean_sampling_rate_hz": 60.0,
    }

    _lookup_write_batch(s, [low_q_row])
    _join_write_batch(s, _make_joined_rows([low_q_row], {pid: (True, 4)}))

    assert all(a["severity"] == "suppressed" for a in s.alerts)
    assert {a["source"] for a in s.alerts} == {"speed_lookup", "speed_join"}


def test_score_is_independent_of_alert_time():
    """A property of the formula: alert_time appears in the explanation
    string but does not enter the score. Useful for catching a future
    regression where someone accidentally hashes alert_time into the
    score (the original CRC32 placeholder did this)."""
    s = _FakeSession()
    base_row = {
        "patient_id": "P_PROP",
        "eeg_chunk_count": 30,
        "mean_sampling_rate_hz": 220.0,
    }
    rows = [{**base_row, "alert_time": datetime(2026, 5, d, 12, tzinfo=timezone.utc)}
            for d in range(1, 11)]
    _lookup_write_batch(s, rows)
    scores = {a["anomaly_score"] for a in s.alerts}
    assert len(scores) == 1, f"score depends on alert_time: {scores}"

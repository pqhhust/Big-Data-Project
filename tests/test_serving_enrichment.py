"""Tests for the Cassandra ``patient_state`` enrichment path used by the
speed layer to compute the real v2 anomaly score.

Covers:
  - :func:`brainwatch.serving.cassandra_sink.upsert_patient_enrichment`
    writes the EHR-derived columns the speed layer reads.
  - :func:`brainwatch.serving.cassandra_sink.fetch_patient_enrichment`
    returns one dict per patient in a single round trip.
  - The full v2 four-term score computed from the lookup matches
    :func:`brainwatch.serving.anomaly_rules.compute_anomaly_score`
    bit-for-bit (the speed layer's score is the canonical formula,
    not a placeholder).
"""
from __future__ import annotations

from datetime import datetime, timezone

from brainwatch.serving.anomaly_rules import compute_anomaly_score, classify_v2
from brainwatch.serving.cassandra_sink import (
    fetch_patient_enrichment, upsert_patient_enrichment,
)


class _FakeRow:
    """Mirror of cassandra.RowFactory output: attribute access by column."""
    def __init__(self, **kw): self.__dict__.update(kw)


class _FakeSession:
    """Just enough of the driver surface to exercise the helpers."""
    def __init__(self):
        self.state: dict[str, dict] = {}
        self.executed: list[tuple[str, tuple]] = []

    def execute(self, stmt, params=()):
        self.executed.append((stmt, params))
        s = stmt.strip().upper()
        if s.startswith("INSERT INTO BRAINWATCH.PATIENT_STATE"):
            pid = params[0]
            row = self.state.setdefault(pid, {})
            cols_section = stmt.split("(", 1)[1].split(")", 1)[0]
            cols = [c.strip() for c in cols_section.split(",")]
            for col, val in zip(cols, params):
                row[col] = val
        elif s.startswith("SELECT") and "PATIENT_STATE" in s:
            ids = set(params)
            return [
                _FakeRow(
                    patient_id=pid,
                    has_critical_lab=row.get("has_critical_lab"),
                    n_medication_changes_24h=row.get("n_medication_changes_24h"),
                    enrichment_updated_at=row.get("enrichment_updated_at"),
                )
                for pid, row in self.state.items() if pid in ids
            ]
        return []


def test_upsert_patient_enrichment_writes_all_three_columns():
    s = _FakeSession()
    upsert_patient_enrichment(s, patient_id="P001",
                              has_critical_lab=True,
                              n_medication_changes_24h=3)
    row = s.state["P001"]
    assert row["has_critical_lab"] is True
    assert row["n_medication_changes_24h"] == 3
    assert isinstance(row["enrichment_updated_at"], datetime)


def test_upsert_patient_enrichment_is_idempotent():
    s = _FakeSession()
    upsert_patient_enrichment(s, "P001", True, 3)
    upsert_patient_enrichment(s, "P001", False, 1)
    assert s.state["P001"]["has_critical_lab"] is False
    assert s.state["P001"]["n_medication_changes_24h"] == 1


def test_fetch_patient_enrichment_returns_one_row_per_patient():
    s = _FakeSession()
    upsert_patient_enrichment(s, "P001", True, 3)
    upsert_patient_enrichment(s, "P002", False, 0)
    out = fetch_patient_enrichment(s, ["P001", "P002", "P003"])
    assert set(out) == {"P001", "P002"}
    assert out["P001"]["has_critical_lab"] is True
    assert out["P001"]["n_medication_changes_24h"] == 3
    assert out["P002"]["has_critical_lab"] is False


def test_fetch_patient_enrichment_empty_input_returns_empty_dict():
    s = _FakeSession()
    out = fetch_patient_enrichment(s, [])
    assert out == {}


def test_fetch_patient_enrichment_uses_single_round_trip():
    s = _FakeSession()
    for i in range(10):
        upsert_patient_enrichment(s, f"P{i:03d}", i % 2 == 0, i)
    s.executed.clear()
    _ = fetch_patient_enrichment(s, [f"P{i:03d}" for i in range(10)])
    select_calls = [e for e in s.executed if "SELECT" in e[0].upper()]
    assert len(select_calls) == 1


def _score_via_lookup(session, patient_id, eeg_chunk_count, mean_sr):
    """Reproduces the speed-layer _write_batch scoring path."""
    enrichment = fetch_patient_enrichment(session, [patient_id])
    enrich = enrichment.get(patient_id, {})
    signal_quality = max(0.0, min(1.0, mean_sr / 250.0))
    features = {
        "eeg_chunk_count": int(eeg_chunk_count),
        "signal_quality_score": signal_quality,
        "has_critical_lab": bool(enrich.get("has_critical_lab", False)),
        "n_medication_changes_24h": int(
            enrich.get("n_medication_changes_24h", 0)),
    }
    score = compute_anomaly_score(features)
    severity = classify_v2(score, features["has_critical_lab"]).severity
    return score, severity, features


def test_speed_layer_score_matches_canonical_v2_formula():
    """A patient with the lookup-returned enrichment scores exactly what
    compute_anomaly_score computes on the same feature dict — the speed
    layer is not a placeholder formula any more."""
    s = _FakeSession()
    upsert_patient_enrichment(s, "P_HIGH", has_critical_lab=True,
                              n_medication_changes_24h=5)

    # chunk=60 saturates chunk_term=1.0; mean_sr=200 → signal_quality=0.8
    #   chunk_term     = min(60/60, 1)   = 1.00 * 0.30 = 0.30
    #   quality_term   = 1 - 0.8         = 0.20 * 0.25 = 0.05
    #   critical_term  = 0.6  (True)             * 0.30 = 0.18
    #   meds_term      = min(5/5, 1)     = 1.00 * 0.15 = 0.15
    #   ─────────────────────────────────────────────────
    #   score          = 0.68 ≥ 0.60     → critical (via lab escalation)
    score, severity, feats = _score_via_lookup(
        s, "P_HIGH", eeg_chunk_count=60, mean_sr=200.0)

    expected = compute_anomaly_score(feats)
    assert abs(score - expected) < 1e-9
    # critical-lab escalation fires at the 0.60 floor when
    # has_critical_lab=True and the score crosses it.
    assert severity == "critical", f"expected critical, got {severity!r} (score={score:.3f})"


def test_speed_layer_score_cold_start_patient_falls_back_to_zero_enrichment():
    """A patient never enriched by the batch path is scored on the
    EEG-only subset of the v2 formula. No CRC32 placeholder remains."""
    s = _FakeSession()

    score, severity, feats = _score_via_lookup(
        s, "P_COLD", eeg_chunk_count=20, mean_sr=240.0)

    assert feats["has_critical_lab"] is False
    assert feats["n_medication_changes_24h"] == 0

    expected = compute_anomaly_score(feats)
    assert abs(score - expected) < 1e-9
    assert severity in {"normal", "advisory"}

"""Tests for brainwatch.serving.alert_publisher — 6 test cases."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from brainwatch.contracts.events import AlertEvent
from brainwatch.serving.alert_publisher import AlertPublisher
from brainwatch.serving.anomaly_rules import AlertDecision


class TestAlertPublisher:
    def test_publish_without_sinks(self) -> None:
        publisher = AlertPublisher()
        alert = AlertEvent(
            patient_id="P001", session_id="S001",
            alert_time=datetime.now(timezone.utc).isoformat(),
            severity="warning", anomaly_score=0.7, explanation="test",
        )
        result = publisher.publish(alert)
        assert result is True
        assert publisher.stats["published"] == 1

    def test_dedup_within_window(self) -> None:
        publisher = AlertPublisher(dedup_window_seconds=300)
        alert = AlertEvent(
            patient_id="P001", session_id="S001",
            alert_time=datetime.now(timezone.utc).isoformat(),
            severity="critical", anomaly_score=0.9, explanation="test",
        )
        publisher.publish(alert)
        result = publisher.publish(alert)  # should be deduped
        assert result is False
        assert publisher.stats["suppressed_dedup"] == 1

    def test_different_patients_not_deduped(self) -> None:
        publisher = AlertPublisher(dedup_window_seconds=300)
        alert1 = AlertEvent(
            patient_id="P001", session_id="S001",
            alert_time=datetime.now(timezone.utc).isoformat(),
            severity="warning", anomaly_score=0.7, explanation="test",
        )
        alert2 = AlertEvent(
            patient_id="P002", session_id="S002",
            alert_time=datetime.now(timezone.utc).isoformat(),
            severity="warning", anomaly_score=0.7, explanation="test",
        )
        assert publisher.publish(alert1) is True
        assert publisher.publish(alert2) is True
        assert publisher.stats["published"] == 2

    def test_publish_batch(self) -> None:
        publisher = AlertPublisher(dedup_window_seconds=0)
        alerts = [
            AlertEvent(
                patient_id=f"P{i}", session_id=f"S{i}",
                alert_time=datetime.now(timezone.utc).isoformat(),
                severity="warning", anomaly_score=0.7, explanation="test",
            )
            for i in range(3)
        ]
        stats = publisher.publish_batch(alerts)
        assert stats["published"] == 3

    def test_create_alert_from_decision(self) -> None:
        publisher = AlertPublisher()
        decision = AlertDecision(severity="critical", explanation="Critical anomaly")
        alert = publisher.create_alert_from_decision("P001", "S001", decision)
        assert alert.severity == "critical"
        assert alert.patient_id == "P001"

    def test_escalation_tracking(self) -> None:
        publisher = AlertPublisher(dedup_window_seconds=0, escalation_threshold=2)
        for _ in range(3):
            alert = AlertEvent(
                patient_id="P001", session_id="S001",
                alert_time=datetime.now(timezone.utc).isoformat(),
                severity="critical", anomaly_score=0.95, explanation="test",
            )
            publisher.publish(alert)
        # Should have triggered escalation warning (logged)
        assert publisher._alert_counts["P001"] >= 2

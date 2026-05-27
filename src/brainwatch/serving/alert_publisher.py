"""Dual-sink alert publisher — writes alerts to both Cassandra and Kafka."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from brainwatch.contracts.events import AlertEvent
from brainwatch.serving.anomaly_rules import AlertDecision

logger = logging.getLogger(__name__)


class AlertPublisher:
    """Publishes anomaly alerts to Cassandra and/or Kafka.

    Features:
    - Dual-sink output (Cassandra + Kafka)
    - Alert deduplication (suppress duplicate alerts within time window)
    - Severity escalation tracking
    - Both batch and streaming modes
    """

    def __init__(
        self,
        cassandra_client: Any = None,
        kafka_writer: Any = None,
        alerts_topic: str = "alerts.anomaly",
        dedup_window_seconds: int = 300,
        escalation_threshold: int = 3,
    ) -> None:
        self.cassandra_client = cassandra_client
        self.kafka_writer = kafka_writer
        self.alerts_topic = alerts_topic
        self.dedup_window_seconds = dedup_window_seconds
        self.escalation_threshold = escalation_threshold

        self._recent_alerts: dict[str, float] = {}  # patient_id → last_alert_timestamp
        self._alert_counts: dict[str, int] = {}  # patient_id → consecutive_alert_count
        self._published = 0
        self._suppressed_dedup = 0

    def publish(self, alert: AlertEvent) -> bool:
        """Publish a single alert with deduplication."""
        # Dedup check
        if self._is_duplicate(alert):
            self._suppressed_dedup += 1
            logger.debug("Alert suppressed (dedup): patient=%s", alert.patient_id)
            return False

        # Track for escalation
        self._track_escalation(alert)

        # Publish to sinks
        success = True
        if self.cassandra_client:
            try:
                self.cassandra_client.insert_alert(alert)
            except Exception:
                logger.exception("Failed to publish alert to Cassandra")
                success = False

        if self.kafka_writer:
            try:
                self.kafka_writer.write(
                    self.alerts_topic,
                    alert.patient_id,
                    json.dumps(alert.to_dict(), default=str),
                )
            except Exception:
                logger.exception("Failed to publish alert to Kafka")
                success = False

        if success:
            self._published += 1
            self._recent_alerts[alert.patient_id] = time.time()

        return success

    def publish_batch(self, alerts: list[AlertEvent]) -> dict[str, int]:
        """Publish multiple alerts, returning statistics."""
        published = 0
        suppressed = 0
        for alert in alerts:
            if self.publish(alert):
                published += 1
            else:
                suppressed += 1

        return {"published": published, "suppressed": suppressed}

    def create_alert_from_decision(
        self,
        patient_id: str,
        session_id: str,
        decision: AlertDecision,
    ) -> AlertEvent:
        """Create an AlertEvent from an anomaly decision."""
        return AlertEvent(
            patient_id=patient_id,
            session_id=session_id,
            alert_time=datetime.now(timezone.utc).isoformat(),
            severity=decision.severity,
            anomaly_score=0.0,  # caller should set this
            explanation=decision.explanation,
        )

    @property
    def stats(self) -> dict[str, int]:
        return {
            "published": self._published,
            "suppressed_dedup": self._suppressed_dedup,
            "tracked_patients": len(self._recent_alerts),
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _is_duplicate(self, alert: AlertEvent) -> bool:
        """Check if alert is a duplicate within the dedup window."""
        last_time = self._recent_alerts.get(alert.patient_id)
        if last_time is None:
            return False
        return (time.time() - last_time) < self.dedup_window_seconds

    def _track_escalation(self, alert: AlertEvent) -> None:
        """Track consecutive alerts for escalation."""
        pid = alert.patient_id
        if alert.severity in ("critical", "warning"):
            self._alert_counts[pid] = self._alert_counts.get(pid, 0) + 1
            if self._alert_counts[pid] >= self.escalation_threshold:
                logger.warning(
                    "ESCALATION: Patient %s has %d consecutive alerts",
                    pid,
                    self._alert_counts[pid],
                )
        else:
            self._alert_counts.pop(pid, None)

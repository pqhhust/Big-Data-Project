"""Feature store — cached feature lookup for real-time scoring."""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class FeatureStore:
    """In-memory feature cache for real-time anomaly scoring.

    Provides fast patient feature lookups with TTL-based expiration
    and optional Cassandra backing.
    """

    def __init__(
        self,
        cassandra_client: Any = None,
        ttl_seconds: int = 300,
    ) -> None:
        self.cassandra_client = cassandra_client
        self.ttl_seconds = ttl_seconds
        self._cache: dict[str, dict[str, Any]] = {}
        self._timestamps: dict[str, float] = {}

    def get(self, patient_id: str) -> dict[str, Any] | None:
        """Get cached features for a patient.

        Returns None if the cache entry has expired or doesn't exist.
        """
        if patient_id in self._cache:
            if time.time() - self._timestamps[patient_id] < self.ttl_seconds:
                return self._cache[patient_id]
            else:
                # Expired
                del self._cache[patient_id]
                del self._timestamps[patient_id]

        # Try Cassandra backing
        if self.cassandra_client and self.cassandra_client.is_connected:
            try:
                features = self._load_from_cassandra(patient_id)
                if features:
                    self.put(patient_id, features)
                    return features
            except Exception:
                logger.exception("Failed to load features from Cassandra")

        return None

    def put(self, patient_id: str, features: dict[str, Any]) -> None:
        """Cache features for a patient."""
        self._cache[patient_id] = features
        self._timestamps[patient_id] = time.time()

    def invalidate(self, patient_id: str) -> None:
        """Remove a patient's features from cache."""
        self._cache.pop(patient_id, None)
        self._timestamps.pop(patient_id, None)

    def clear(self) -> None:
        """Clear the entire cache."""
        self._cache.clear()
        self._timestamps.clear()

    @property
    def size(self) -> int:
        """Return the number of cached entries."""
        return len(self._cache)

    def get_all_cached(self) -> dict[str, dict[str, Any]]:
        """Return all non-expired cached entries."""
        now = time.time()
        return {
            pid: features
            for pid, features in self._cache.items()
            if now - self._timestamps.get(pid, 0) < self.ttl_seconds
        }

    def _load_from_cassandra(self, patient_id: str) -> dict[str, Any] | None:
        """Load features from Cassandra backing store."""
        # This would query the features table
        # Placeholder — actual implementation depends on Cassandra schema
        return None

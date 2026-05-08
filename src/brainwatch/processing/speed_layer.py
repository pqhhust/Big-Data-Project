"""Speed layer — real-time stream-stream join + alert generation.

Owner: **Quang-Hung** (lead).
Depends on: Kim-Quan's anomaly rules v2 + alert publisher.

Reads bronze Parquet as **streaming** sources (NOT Kafka — bronze is already
authoritative and avoids re-parsing JSON), joins EEG + EHR with watermarks,
runs anomaly scoring, writes alerts to Cassandra (via Kim-Quan's publisher)
and to the ``alerts.anomaly`` Kafka topic.
"""
from __future__ import annotations

from typing import Any


def build_streaming_pipeline(
    spark: Any,
    bronze_path: str,
    checkpoint_path: str,
    kafka_servers: str,
    cassandra_contact_points: str,
):
    """Wire up the full speed-layer query.

    Quang-Hung: implement.

    Stage 1 — read bronze as streaming sources:
      - ``spark.readStream.format("parquet").schema(...).load(f"{bronze_path}/eeg")``
      - same for EHR.
      - ``.withWatermark("event_time", "10 minutes")`` for EEG.
      - ``.withWatermark("event_time", "30 minutes")`` for EHR.

    Stage 2 — stream-stream left-outer join on patient_id within ±30 min.
      Use the time-bound join idiom; without it Spark holds unbounded state.

    Stage 3 — windowed aggregation (1-min tumbling, 30s slide):
      - count(*) as eeg_chunk_count
      - avg(sampling_rate_hz) as mean_sampling_rate_hz
      - max(when(event_type='critical_lab',1).otherwise(0)) as has_critical_lab
      - max(window_seconds), max(channel_count) for QC

    Stage 4 — anomaly scoring (call Kim-Quan's
    ``brainwatch.serving.anomaly_rules.compute_anomaly_score``).
      Wrap as a UDF (``F.udf(..., FloatType())``) so it runs on rows.

    Stage 5 — write via foreachBatch:
      - delegate to ``brainwatch.serving.alert_publisher.publish_alerts``
        which handles the dual write (Cassandra + Kafka topic).
      - checkpoint at ``{checkpoint_path}/speed_layer``.
      - ``trigger(processingTime="30 seconds")``.

    Return the ``StreamingQuery`` so the caller can ``.awaitTermination()``.
    """
    # Quang-Hung: code the speed-layer pipeline here.
    pass


def main() -> None:
    """CLI: ``python -m brainwatch.processing.speed_layer
    --bronze data/lake/bronze --checkpoint data/checkpoints
    --kafka kafka:9092 --cassandra cassandra-svc``.

    Quang-Hung: argparse, build SparkSession with kafka + cassandra packages,
    call build_streaming_pipeline, awaitTermination.
    """
    # Quang-Hung: code the speed-layer entry point here.
    pass


if __name__ == "__main__":
    main()

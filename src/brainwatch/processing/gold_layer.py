"""Silver → Gold Spark batch jobs.

Owner: **Kim-Hung**.
Reads ``data/lake/silver/{eeg,ehr,_dim/patient}`` and writes business-ready
feature tables to ``data/lake/gold/``.

This is the layer that powers Trang's dashboard and serves as the export
target for batch-recomputed features (the batch half of the Lambda design).
"""
from __future__ import annotations

from typing import Any


def build_patient_features(spark: Any, silver_path: str, gold_path: str) -> None:
    """Per-patient daily rollups joined with EHR context.

    Kim-Hung: implement.
      1. ``eeg = spark.read.parquet(f"{silver_path}/eeg")``
      2. ``ehr = spark.read.parquet(f"{silver_path}/ehr")``
      3. ``patient_dim = spark.read.parquet(f"{silver_path}/_dim/patient")``
      4. ``broadcast(patient_dim)`` join on ``patient_id`` — small dim, force
         broadcast.
      5. left-join EHR within ±30 min:
         ``ehr.event_time BETWEEN eeg.event_time - INTERVAL 30 MINUTES
                              AND eeg.event_time + INTERVAL 30 MINUTES``.
      6. groupBy ``patient_id, to_date(event_time) as event_date``:
         - ``count(*) as n_eeg_chunks``
         - ``avg(sampling_rate_hz) as mean_sampling_rate``
         - ``max(when(event_type='critical_lab',1).otherwise(0))
              as has_critical_lab_today``
         - ``sum(when(event_type='medication',1).otherwise(0))
              as n_medication_changes``
      7. write Parquet to ``{gold_path}/patient_features`` partitioned by
         ``event_date``.

    Tip: call ``df.explain()`` and confirm the patient_dim join is
    BroadcastHashJoin, not SortMergeJoin. If it's SMJ, force with
    ``F.broadcast(patient_dim)``.
    """
    # Kim-Hung: code the patient features rollup here.
    pass


def build_alert_summary(spark: Any, gold_path: str,
                        alerts_export_path: str | None = None) -> None:
    """Daily alert counts by severity.

    Kim-Hung: implement.
      Two input options (pick whichever Kim-Quan delivers first):
        (a) Read ``alerts_export_path`` Parquet (if Kim-Quan exposes a
            Cassandra → Parquet export).
        (b) Read directly from Cassandra via the spark-cassandra connector
            (heavier dep — only if needed).
      Then:
        groupBy ``to_date(alert_time), severity`` → count, write to
        ``{gold_path}/alert_summary`` partitioned by ``alert_date``.
    """
    # Kim-Hung: code the alert summary builder here.
    pass


def main() -> None:
    """CLI: ``python -m brainwatch.processing.gold_layer
    --silver data/lake/silver --gold data/lake/gold``.

    Kim-Hung: argparse, build SparkSession, call build_patient_features and
    build_alert_summary in order.
    """
    # Kim-Hung: code the main entry here.
    pass


if __name__ == "__main__":
    main()

"""Bronze → Silver Spark batch jobs.

Owner: **Kim-Hung**.
Reads partitioned Parquet from ``data/lake/bronze/`` and writes deduplicated /
quality-flagged Parquet to ``data/lake/silver/``.

PySpark imports MUST be deferred so this module is importable without spark.
"""
from __future__ import annotations

from typing import Any


def build_eeg_silver(spark: Any, bronze_path: str, silver_path: str) -> None:
    """Bronze EEG → Silver EEG.

    Kim-Hung: implement.
      1. ``spark.read.parquet(f"{bronze_path}/eeg")``
      2. dedup on ``(patient_id, session_id, event_time)`` using
         ``dropDuplicates([...])``.
      3. drop rows where ``sampling_rate_hz <= 0 or sampling_rate_hz > 1000``.
      4. add ``quality_flag`` column:
            - ``"LOW_SR"`` when sampling_rate_hz < 100
            - ``"SHORT_WINDOW"`` when window_seconds < 5
            - ``"OK"`` otherwise
         (use ``when().otherwise()`` chains).
      5. write Parquet to ``{silver_path}/eeg``, partition by
         ``site_id, ingestion_date``, mode ``overwrite``.
      6. coalesce to keep file count reasonable (target 64–256 MB/file).
    """
    # Kim-Hung: code the EEG silver builder here.
    pass


def build_ehr_silver(spark: Any, bronze_path: str, silver_path: str) -> None:
    """Bronze EHR → Silver EHR (latest version per encounter).

    Kim-Hung: implement.
      1. read ``{bronze_path}/ehr``.
      2. window: ``Window.partitionBy("patient_id", "encounter_id")
         .orderBy(F.col("version").desc())``.
      3. ``df.withColumn("rn", row_number().over(window)).filter("rn = 1")``.
      4. drop the ``rn`` helper column.
      5. write Parquet to ``{silver_path}/ehr`` partitioned by
         ``ingestion_date``, mode ``overwrite``.
    """
    # Kim-Hung: code the EHR silver builder here.
    pass


def build_patient_dim(spark: Any, silver_path: str) -> None:
    """Build a small patient dimension table from the union of EEG + EHR.

    Kim-Hung: implement.
      1. read ``{silver_path}/eeg`` and ``{silver_path}/ehr``.
      2. union ``select("patient_id")`` from both, then ``distinct()``.
      3. add stable ``patient_key`` (sha1 of patient_id, first 12 hex chars).
      4. write to ``{silver_path}/_dim/patient`` (small enough for broadcast).
    """
    # Kim-Hung: code the patient dim builder here.
    pass


def main() -> None:
    """CLI entry: ``python -m brainwatch.processing.silver_layer
    --bronze data/lake/bronze --silver data/lake/silver``.

    Kim-Hung: argparse, build SparkSession, call the three builders in order.
    """
    # Kim-Hung: code the main entry here.
    pass


if __name__ == "__main__":
    main()

"""Bronze → Silver Spark batch jobs.

Reads partitioned Parquet/JSONL from ``data/lake/bronze/`` and writes
deduplicated, quality-flagged Parquet to ``data/lake/silver/``.

PySpark imports are deferred so this module is importable without Spark.
"""
from __future__ import annotations

import argparse
import hashlib
from typing import Any


_BRONZE_EEG_RELATIVE = "eeg"
_BRONZE_EHR_RELATIVE = "ehr"


def _read_bronze(spark: Any, path: str):
    """Read bronze files transparently — JSONL produced by ``BronzeWriter``
    or Parquet produced by the Structured Streaming sink.

    The format is detected by sniffing the first matching file via Python
    ``os.walk`` at the driver (cheap, deterministic, single-machine).
    """
    import os as _os

    sniff_ext = None
    for dirpath, _, filenames in _os.walk(path):
        for fn in filenames:
            if fn.endswith(".jsonl") or fn.endswith(".json"):
                sniff_ext = "json"
                break
            if fn.endswith(".parquet"):
                sniff_ext = "parquet"
                break
        if sniff_ext is not None:
            break

    if sniff_ext == "json":
        return (
            spark.read
                 .option("recursiveFileLookup", "true")
                 .option("pathGlobFilter", "*.jsonl")
                 .json(path)
        )
    return spark.read.parquet(path)


def build_eeg_silver(spark: Any, bronze_path: str, silver_path: str) -> None:
    """Bronze EEG → Silver EEG: dedup + quality flag + bad-row filter."""
    from pyspark.sql import functions as F

    df = _read_bronze(spark, f"{bronze_path}/{_BRONZE_EEG_RELATIVE}")
    df = df.dropDuplicates(["patient_id", "session_id", "event_time"])
    df = df.filter((F.col("sampling_rate_hz") > 0) & (F.col("sampling_rate_hz") <= 1000))
    df = df.withColumn(
        "quality_flag",
        F.when(F.col("sampling_rate_hz") < 100, F.lit("LOW_SR"))
         .when(F.col("window_seconds") < 5, F.lit("SHORT_WINDOW"))
         .otherwise(F.lit("OK")),
    )
    if "event_time" in df.columns:
        df = df.withColumn("ingestion_date", F.to_date("event_time"))
    (
        df.coalesce(4)
          .write.mode("overwrite")
          .partitionBy("site_id", "ingestion_date")
          .parquet(f"{silver_path}/eeg")
    )


def build_ehr_silver(spark: Any, bronze_path: str, silver_path: str) -> None:
    """Bronze EHR → Silver EHR: latest version per (patient_id, encounter_id)."""
    from pyspark.sql import functions as F
    from pyspark.sql.window import Window

    df = _read_bronze(spark, f"{bronze_path}/{_BRONZE_EHR_RELATIVE}")
    if "version" not in df.columns:
        df = df.withColumn("version", F.lit(1))
    window = Window.partitionBy("patient_id", "encounter_id").orderBy(F.col("version").desc())
    latest = (
        df.withColumn("rn", F.row_number().over(window))
          .filter(F.col("rn") == 1)
          .drop("rn")
    )
    if "ingestion_date" not in latest.columns and "event_time" in latest.columns:
        latest = latest.withColumn("ingestion_date", F.to_date("event_time"))
    (
        latest.coalesce(2)
              .write.mode("overwrite")
              .partitionBy("ingestion_date")
              .parquet(f"{silver_path}/ehr")
    )


def build_patient_dim(spark: Any, silver_path: str) -> None:
    """Patient dimension table — small enough for broadcast joins downstream."""
    from pyspark.sql import functions as F

    eeg_patients = spark.read.parquet(f"{silver_path}/eeg").select("patient_id")
    ehr_patients = spark.read.parquet(f"{silver_path}/ehr").select("patient_id")
    patients = eeg_patients.union(ehr_patients).distinct()

    @F.udf("string")
    def _patient_key(pid: str) -> str:
        if pid is None:
            return None
        return hashlib.sha1(pid.encode("utf-8")).hexdigest()[:12]

    patients = patients.withColumn("patient_key", _patient_key(F.col("patient_id")))
    (
        patients.coalesce(1)
                .write.mode("overwrite")
                .parquet(f"{silver_path}/_dim/patient")
    )


def main() -> None:
    """CLI: ``python -m brainwatch.processing.silver_layer --bronze ... --silver ...``."""
    from pyspark.sql import SparkSession

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bronze", default="data/lake/bronze")
    parser.add_argument("--silver", default="data/lake/silver")
    args = parser.parse_args()

    spark = (
        SparkSession.builder
        .appName("brainwatch-silver")
        .config("spark.sql.shuffle.partitions", "8")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("WARN")

    build_eeg_silver(spark, args.bronze, args.silver)
    build_ehr_silver(spark, args.bronze, args.silver)
    build_patient_dim(spark, args.silver)


if __name__ == "__main__":
    main()

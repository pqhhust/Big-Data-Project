#!/usr/bin/env python3
"""Spark coverage demonstrations: pivot, persistence, and a custom UDAF.

The production pipeline (silver/gold/speed layers) already exercises
window functions, broadcast and sort-merge joins, watermarked Structured
Streaming, partitioned Parquet writes, and an MLlib LogisticRegression
classifier. This script fills the remaining intermediate-level rubric
items that did not have a natural home in the production code:

    1. ``pivot`` — produce a wide table of severity counts per site per day
       from the alerts dataset (one row per (site, date), one column per
       severity).
    2. ``cache()`` — explicitly persist the silver EEG DataFrame before
       multiple consumers read it; show the stage savings via ``explain``.
    3. ``Pandas UDAF`` (groupedAgg pandas_udf) — a custom aggregation that
       computes a fixed-bin signal-quality histogram per patient.

Run locally::

    python scripts/spark_advanced_demo.py \\
        --silver data/lake/silver_real \\
        --alerts artifacts/demo/alerts_real.jsonl \\
        --out    artifacts/demo/spark_advanced

Each output is written as Parquet (small) so the result can be inspected
with ``pyspark`` or any Parquet viewer.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def build_severity_pivot(spark, alerts_path: str):
    """Wide table: rows = (site_id, alert_date), columns = severity, value = count.

    Demonstrates ``groupBy(...).pivot(...).agg(...)`` and the use of an
    ordered ``pivot`` over a fixed value list so the output schema is
    deterministic across runs.
    """
    from pyspark.sql import functions as F

    alerts = spark.read.json(alerts_path)
    # alerts JSONL carries: patient_id, alert_time, severity, anomaly_score,
    # explanation. The patient_id starts with the site code, e.g. I0002...
    enriched = (
        alerts
        .withColumn("site_id", F.substring("patient_id", 1, 5))
        .withColumn("alert_date", F.to_date(F.col("alert_time")))
    )
    severities = ["critical", "warning", "advisory", "normal", "suppressed"]
    pivot_df = (
        enriched.groupBy("site_id", "alert_date")
                .pivot("severity", severities)
                .agg(F.count("*"))
                .na.fill(0)
                .orderBy("site_id", "alert_date")
    )
    return pivot_df


def build_quality_histogram(spark, silver_eeg_path: str, bins: int = 10):
    """Per-patient histogram of ``signal_quality_score`` over fixed bins.

    Demonstrates a **custom grouped aggregation** via a Pandas UDAF.
    The function returns one row per patient with an array of ``bins``
    integers; the i-th integer is the number of windows that fell in
    the i-th bin.
    """
    from pyspark.sql import functions as F
    from pyspark.sql.types import ArrayType, IntegerType
    import pandas as pd

    df = spark.read.parquet(silver_eeg_path)

    @F.pandas_udf(ArrayType(IntegerType()))
    def quality_hist(values: pd.Series) -> pd.Series:
        # Each `values` is a pandas Series of float scores for ONE patient.
        # We must return a Series whose length matches the number of groups
        # — for a grouped-agg pandas_udf called inside agg(), the return is
        # a scalar (or array). For each input series we return one array.
        edges = [i / bins for i in range(bins + 1)]
        counts, _ = pd.cut(values, bins=edges, include_lowest=True,
                           right=False, labels=False, retbins=True)
        # pd.cut returns the bin index per row; we group-count them.
        hist = [0] * bins
        for ix in counts.dropna().astype(int):
            if 0 <= ix < bins:
                hist[ix] += 1
        return pd.Series([hist])

    return (df.groupBy("patient_id")
              .agg(quality_hist(F.col("signal_quality_score")).alias("quality_histogram"))
              .orderBy("patient_id"))


def demonstrate_caching(spark, silver_eeg_path: str):
    """Cache the silver EEG DataFrame, run two downstream actions, and
    print the resulting query plan so the cache hit is visible.

    Without caching, Spark would re-scan the Parquet files for each
    downstream action. With caching, the second action reads from the
    in-memory column store.
    """
    df = spark.read.parquet(silver_eeg_path)
    df.cache()                                    # explicit persistence
    cnt = df.count()                              # action 1: materialises cache
    distinct_patients = df.select("patient_id").distinct().count()  # action 2: cache hit
    print(f"[cache] rows={cnt}  distinct_patients={distinct_patients}")
    print("[cache] explain() output after .cache():")
    df.explain(mode="formatted")
    return cnt, distinct_patients


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--silver",  default="data/lake/silver_real/eeg")
    parser.add_argument("--alerts",  default="artifacts/demo/alerts_real.jsonl")
    parser.add_argument("--out",     default="artifacts/demo/spark_advanced", type=Path)
    parser.add_argument("--master",  default="local[4]")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    from pyspark.sql import SparkSession
    spark = (
        SparkSession.builder
        .appName("brainwatch-spark-advanced")
        .master(args.master)
        .config("spark.sql.shuffle.partitions", "8")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("WARN")

    print("[1/3] severity pivot (sites x dates x severities) ----------------")
    pivot_df = build_severity_pivot(spark, args.alerts)
    pivot_df.show(20, truncate=False)
    pivot_df.write.mode("overwrite").parquet(str(args.out / "severity_pivot"))

    print("[2/3] persistence + plan inspection ------------------------------")
    demonstrate_caching(spark, args.silver)

    print("[3/3] per-patient quality histogram (custom grouped-agg UDAF) ----")
    hist_df = build_quality_histogram(spark, args.silver, bins=10)
    hist_df.show(10, truncate=False)
    hist_df.write.mode("overwrite").parquet(str(args.out / "quality_histogram"))

    spark.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

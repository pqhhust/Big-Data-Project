"""Spark session and utility helpers for BrainWatch pipelines."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def get_or_create_spark_session(
    app_name: str = "BrainWatch",
    master: str = "local[*]",
    driver_memory: str = "2g",
    shuffle_partitions: int = 8,
    log_level: str = "WARN",
    extra_configs: dict[str, str] | None = None,
) -> Any:
    """Create or retrieve a SparkSession with BrainWatch defaults.

    PySpark is imported at runtime so the module is importable without Spark.
    """
    from pyspark.sql import SparkSession

    builder = (
        SparkSession.builder
        .appName(app_name)
        .master(master)
        .config("spark.driver.memory", driver_memory)
        .config("spark.sql.shuffle.partitions", str(shuffle_partitions))
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.serializer", "org.apache.spark.serializer.KryoSerializer")
    )

    if extra_configs:
        for key, value in extra_configs.items():
            builder = builder.config(key, value)

    spark = builder.getOrCreate()
    spark.sparkContext.setLogLevel(log_level)
    logger.info("SparkSession ready: app=%s master=%s", app_name, master)
    return spark


def configure_checkpoint(spark: Any, checkpoint_dir: str) -> None:
    """Set the Spark checkpoint directory."""
    spark.sparkContext.setCheckpointDir(checkpoint_dir)
    logger.info("Checkpoint directory set to %s", checkpoint_dir)


def log_explain_plan(df: Any, label: str = "") -> str:
    """Capture and log the physical execution plan of a DataFrame.

    Returns the explain string for programmatic inspection.
    """
    plan = df._jdf.queryExecution().simpleString()
    logger.info("Explain plan [%s]:\n%s", label, plan)
    return plan


def count_partitions(df: Any) -> int:
    """Return the number of RDD partitions in a DataFrame."""
    return df.rdd.getNumPartitions()


def repartition_by_date(df: Any, date_col: str = "date", num_partitions: int | None = None) -> Any:
    """Repartition a DataFrame by a date column for partition pruning."""
    from pyspark.sql import functions as F

    if num_partitions:
        return df.repartition(num_partitions, F.col(date_col))
    return df.repartition(F.col(date_col))


def cache_and_count(df: Any, label: str = "DataFrame") -> int:
    """Cache a DataFrame and log its row count."""
    df.cache()
    n = df.count()
    logger.info("%s cached: %d rows, %d partitions", label, n, count_partitions(df))
    return n

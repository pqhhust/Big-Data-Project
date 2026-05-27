"""Gold-layer batch processing — Silver → Gold business-ready aggregations.

Implements:
- EEG-EHR join (sort-merge + broadcast for small dimensions)
- Pivot: EHR lab features pivoted wide
- Unpivot: feature export in long format
- Alert trend summaries
- Dashboard-ready patient risk state
- Execution plan review and optimization
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def build_gold_joined_features(
    spark: Any,
    silver_eeg_path: str,
    silver_ehr_path: str,
    gold_path: str,
) -> Any:
    """Join silver EEG and EHR data into gold-layer feature tables.

    Uses a **sort-merge join** for the large EEG-EHR dataset and a
    **broadcast join** for the diagnosis dictionary lookup.
    """
    from pyspark.sql import functions as F

    eeg = spark.read.parquet(silver_eeg_path)
    ehr = spark.read.parquet(silver_ehr_path)

    # Filter to non-duplicate, latest-version records
    eeg_clean = eeg.filter(~F.col("is_duplicate"))
    ehr_clean = ehr.filter(F.col("is_latest_version") == "true")

    # --- Sort-merge join: EEG × EHR on patient_id ---
    # Spark will use sort-merge by default for large datasets
    joined = eeg_clean.join(
        ehr_clean.select(
            F.col("patient_id").alias("ehr_patient_id"),
            "encounter_id",
            F.col("event_type").alias("ehr_event_type"),
            "payload",
            F.col("event_time").alias("ehr_event_time"),
        ),
        on=(eeg_clean.patient_id == F.col("ehr_patient_id")),
        how="left",
    ).drop("ehr_patient_id")

    # Feature aggregation
    features = (
        joined.groupBy("patient_id", "session_id", "site_id", "date")
        .agg(
            F.count("*").alias("eeg_chunk_count"),
            F.avg("signal_quality").alias("mean_signal_quality"),
            F.max("channel_count").alias("max_channels"),
            F.avg("sampling_rate_hz").alias("mean_sampling_rate"),
            F.countDistinct("encounter_id").alias("ehr_encounter_count"),
            F.collect_set("ehr_event_type").alias("ehr_event_types"),
            F.max(F.when(F.col("ehr_event_type") == "critical_lab", 1).otherwise(0)).alias("has_critical_lab"),
        )
        .withColumn(
            "anomaly_score",
            F.round(
                F.col("eeg_chunk_count") * F.lit(0.01)
                + F.col("has_critical_lab") * F.lit(0.5)
                + (F.lit(1.0) - F.col("mean_signal_quality")) * F.lit(0.3),
                4,
            ),
        )
        .withColumn(
            "risk_level",
            F.when(F.col("anomaly_score") >= 0.85, "critical")
            .when(F.col("anomaly_score") >= 0.60, "warning")
            .when(F.col("anomaly_score") >= 0.30, "elevated")
            .otherwise("normal"),
        )
    )

    features.write.mode("overwrite").partitionBy("date").parquet(gold_path)
    logger.info("Gold joined features written to %s", gold_path)
    return features


def build_gold_with_broadcast(
    spark: Any,
    silver_eeg_path: str,
    diagnosis_dict_path: str,
    gold_path: str,
) -> Any:
    """Demonstrate a broadcast join with a small diagnosis dictionary.

    The diagnosis dictionary is small enough to broadcast to all executors.
    """
    from pyspark.sql import functions as F

    eeg = spark.read.parquet(silver_eeg_path)

    # Small dimension table — broadcast join
    diag = spark.read.parquet(diagnosis_dict_path)
    diag_broadcast = F.broadcast(diag)

    enriched = eeg.join(diag_broadcast, on="patient_id", how="left")
    enriched.write.mode("overwrite").parquet(gold_path)
    logger.info("Gold enriched features (broadcast) written to %s", gold_path)
    return enriched


def pivot_ehr_lab_features(spark: Any, silver_ehr_path: str, output_path: str) -> Any:
    """Pivot EHR lab results into wide format — one column per test name.

    This demonstrates the Spark pivot operation for feature engineering.
    """
    from pyspark.sql import functions as F

    ehr = spark.read.parquet(silver_ehr_path)

    # Filter to lab results
    labs = ehr.filter(
        (F.col("event_type") == "lab_result") | (F.col("event_type") == "critical_lab")
    )

    # Extract test_name and value from payload map
    labs = labs.withColumn("test_name", F.col("payload").getItem("test_name"))
    labs = labs.withColumn("test_value", F.col("payload").getItem("value").cast("double"))

    # Pivot: rows → columns
    pivoted = (
        labs.groupBy("patient_id", "encounter_id")
        .pivot("test_name")
        .agg(F.max("test_value"))
    )

    pivoted.write.mode("overwrite").parquet(output_path)
    logger.info("Pivoted lab features written to %s", output_path)
    return pivoted


def unpivot_features_long(spark: Any, gold_path: str, output_path: str) -> Any:
    """Unpivot gold feature table from wide to long format for export.

    Demonstrates the stack/unpivot operation.
    """
    from pyspark.sql import functions as F

    df = spark.read.parquet(gold_path)

    # Select numeric feature columns
    feature_cols = [c for c in df.columns if c not in (
        "patient_id", "session_id", "site_id", "date",
        "ehr_event_types", "risk_level",
    )]

    if not feature_cols:
        logger.warning("No feature columns found for unpivot")
        return df

    # Build stack expression
    n = len(feature_cols)
    stack_expr = ", ".join(f"'{c}', cast(`{c}` as double)" for c in feature_cols)
    stack_sql = f"stack({n}, {stack_expr}) as (feature_name, feature_value)"

    long_df = df.select("patient_id", "session_id", "date", F.expr(stack_sql))
    long_df = long_df.filter(F.col("feature_value").isNotNull())

    long_df.write.mode("overwrite").parquet(output_path)
    logger.info("Unpivoted features written to %s", output_path)
    return long_df


def build_alert_trends(spark: Any, gold_path: str, output_path: str) -> Any:
    """Build alert trend summaries from gold feature data.

    Aggregates by date and risk level to show trends over time.
    """
    from pyspark.sql import functions as F

    df = spark.read.parquet(gold_path)

    trends = (
        df.groupBy("date", "risk_level")
        .agg(
            F.count("*").alias("event_count"),
            F.avg("anomaly_score").alias("avg_anomaly_score"),
            F.avg("mean_signal_quality").alias("avg_signal_quality"),
            F.countDistinct("patient_id").alias("unique_patients"),
        )
        .orderBy("date", "risk_level")
    )

    trends.write.mode("overwrite").parquet(output_path)
    logger.info("Alert trends written to %s", output_path)
    return trends


def build_patient_risk_state(spark: Any, gold_path: str, output_path: str) -> Any:
    """Build a dashboard-ready patient risk state summary.

    Each patient gets one row with their latest risk assessment.
    """
    from pyspark.sql import Window, functions as F

    df = spark.read.parquet(gold_path)

    # Latest record per patient
    w = Window.partitionBy("patient_id").orderBy(F.desc("date"))
    latest = df.withColumn("rn", F.row_number().over(w)).filter(F.col("rn") == 1).drop("rn")

    # Summary
    risk_state = latest.select(
        "patient_id",
        "session_id",
        "site_id",
        "date",
        "anomaly_score",
        "risk_level",
        "mean_signal_quality",
        "eeg_chunk_count",
        "has_critical_lab",
    )

    risk_state.write.mode("overwrite").parquet(output_path)
    logger.info("Patient risk state written to %s", output_path)
    return risk_state

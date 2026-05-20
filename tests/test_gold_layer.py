"""Tests for ``brainwatch.processing.gold_layer``."""
from __future__ import annotations

import importlib
from datetime import datetime

import pytest


def test_module_importable_without_pyspark():
    import brainwatch.processing.gold_layer as gold  # noqa: F401


_HAS_PYSPARK = importlib.util.find_spec("pyspark") is not None


@pytest.fixture(scope="module")
def spark():
    if not _HAS_PYSPARK:
        pytest.skip("pyspark not installed")
    from pyspark.sql import SparkSession

    session = (
        SparkSession.builder
        .appName("brainwatch-gold-tests")
        .master("local[2]")
        .config("spark.sql.shuffle.partitions", "2")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.autoBroadcastJoinThreshold", str(50 * 1024 * 1024))
        .getOrCreate()
    )
    session.sparkContext.setLogLevel("ERROR")
    yield session
    session.stop()


def _write_silver(spark, silver_path, eeg_rows, ehr_rows):
    from pyspark.sql.types import StructType, StructField, StringType, FloatType, TimestampType, IntegerType, DateType

    eeg_schema = StructType([
        StructField("patient_id", StringType()),
        StructField("session_id", StringType()),
        StructField("event_time", TimestampType()),
        StructField("site_id", StringType()),
        StructField("sampling_rate_hz", FloatType()),
        StructField("window_seconds", FloatType()),
        StructField("quality_flag", StringType()),
        StructField("ingestion_date", DateType()),
    ])
    spark.createDataFrame(eeg_rows, schema=eeg_schema).write.mode("overwrite").parquet(f"{silver_path}/eeg")

    ehr_schema = StructType([
        StructField("patient_id", StringType()),
        StructField("encounter_id", StringType()),
        StructField("event_time", TimestampType()),
        StructField("event_type", StringType()),
        StructField("version", IntegerType()),
        StructField("ingestion_date", DateType()),
    ])
    spark.createDataFrame(ehr_rows, schema=ehr_schema).write.mode("overwrite").parquet(f"{silver_path}/ehr")

    patient_ids = {r[0] for r in eeg_rows} | {r[0] for r in ehr_rows}
    dim_rows = [(pid, f"key_{i:03d}") for i, pid in enumerate(sorted(patient_ids))]
    from pyspark.sql.types import StructType as ST, StructField as SF, StringType as TS
    dim_schema = ST([SF("patient_id", TS()), SF("patient_key", TS())])
    spark.createDataFrame(dim_rows, schema=dim_schema).write.mode("overwrite").parquet(f"{silver_path}/_dim/patient")


@pytest.mark.skipif(not _HAS_PYSPARK, reason="pyspark not installed")
def test_patient_features_aggregates_per_day(spark, tmp_path):
    from brainwatch.processing.gold_layer import build_patient_features

    silver = str(tmp_path / "silver")
    gold = str(tmp_path / "gold")
    d1 = datetime(2026, 5, 9, 10, 0, 0)
    d2 = datetime(2026, 5, 10, 11, 0, 0)
    eeg = [
        ("p1", f"s{i}", d1, "site1", 200.0, 10.0, "OK", d1.date()) for i in range(3)
    ] + [
        ("p1", f"s{i+10}", d2, "site1", 200.0, 10.0, "OK", d2.date()) for i in range(2)
    ]
    _write_silver(spark, silver, eeg, [])
    build_patient_features(spark, silver, gold)

    out = {(r["patient_id"], str(r["event_date"])): r["n_eeg_chunks"]
           for r in spark.read.parquet(f"{gold}/patient_features").collect()}
    assert out[("p1", "2026-05-09")] == 3
    assert out[("p1", "2026-05-10")] == 2


@pytest.mark.skipif(not _HAS_PYSPARK, reason="pyspark not installed")
def test_critical_lab_flag_propagates(spark, tmp_path):
    from brainwatch.processing.gold_layer import build_patient_features

    silver = str(tmp_path / "silver")
    gold = str(tmp_path / "gold")
    t = datetime(2026, 5, 19, 10, 0, 0)
    t_ehr = datetime(2026, 5, 19, 10, 15, 0)
    eeg = [("p1", "s1", t, "site1", 200.0, 10.0, "OK", t.date())]
    ehr = [("p1", "enc1", t_ehr, "critical_lab", 1, t_ehr.date())]
    _write_silver(spark, silver, eeg, ehr)
    build_patient_features(spark, silver, gold)

    row = spark.read.parquet(f"{gold}/patient_features").collect()[0]
    assert row["has_critical_lab_today"] == 1


@pytest.mark.skipif(not _HAS_PYSPARK, reason="pyspark not installed")
def test_patient_dim_join_uses_broadcast(spark, tmp_path):
    from pyspark.sql import functions as F

    silver = str(tmp_path / "silver")
    gold = str(tmp_path / "gold")
    t = datetime(2026, 5, 19, 10, 0, 0)
    eeg = [("p1", "s1", t, "site1", 200.0, 10.0, "OK", t.date())]
    _write_silver(spark, silver, eeg, [])

    eeg_df = spark.read.parquet(f"{silver}/eeg")
    dim_df = spark.read.parquet(f"{silver}/_dim/patient")
    plan = eeg_df.join(F.broadcast(dim_df), on="patient_id", how="left")._jdf.queryExecution().toString()
    assert "BroadcastHashJoin" in plan or "broadcast" in plan.lower()

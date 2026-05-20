"""Tests for ``brainwatch.processing.silver_layer``.

The full-fat tests need a SparkSession; they skip cleanly when pyspark is
not installed so the suite stays green on minimal environments.
"""
from __future__ import annotations

import importlib

import pytest


def test_module_importable_without_pyspark():
    """The module must import even when pyspark is missing — keep heavy
    imports inside the function bodies."""
    import brainwatch.processing.silver_layer as silver  # noqa: F401


_HAS_PYSPARK = importlib.util.find_spec("pyspark") is not None


@pytest.fixture(scope="module")
def spark():
    if not _HAS_PYSPARK:
        pytest.skip("pyspark not installed")
    from pyspark.sql import SparkSession

    session = (
        SparkSession.builder
        .appName("brainwatch-silver-tests")
        .master("local[2]")
        .config("spark.sql.shuffle.partitions", "2")
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )
    session.sparkContext.setLogLevel("ERROR")
    yield session
    session.stop()


def _write_bronze_eeg(spark, path, rows):
    from pyspark.sql.types import StructType, StructField, StringType, FloatType, TimestampType
    schema = StructType([
        StructField("patient_id", StringType()),
        StructField("session_id", StringType()),
        StructField("event_time", TimestampType()),
        StructField("site_id", StringType()),
        StructField("sampling_rate_hz", FloatType()),
        StructField("window_seconds", FloatType()),
    ])
    df = spark.createDataFrame(rows, schema=schema)
    df.write.mode("overwrite").parquet(f"{path}/eeg")


def _write_bronze_ehr(spark, path, rows):
    from pyspark.sql.types import StructType, StructField, StringType, IntegerType, TimestampType
    schema = StructType([
        StructField("patient_id", StringType()),
        StructField("encounter_id", StringType()),
        StructField("event_time", TimestampType()),
        StructField("event_type", StringType()),
        StructField("version", IntegerType()),
    ])
    df = spark.createDataFrame(rows, schema=schema)
    df.write.mode("overwrite").parquet(f"{path}/ehr")


@pytest.mark.skipif(not _HAS_PYSPARK, reason="pyspark not installed")
def test_eeg_silver_dedups_on_patient_session_event_time(spark, tmp_path):
    from brainwatch.processing.silver_layer import build_eeg_silver
    from datetime import datetime

    bronze = str(tmp_path / "bronze")
    silver = str(tmp_path / "silver")
    t = datetime(2026, 5, 19, 10, 0, 0)
    rows = [
        ("p1", "s1", t, "site1", 200.0, 10.0),
        ("p1", "s1", t, "site1", 200.0, 10.0),
        ("p2", "s2", t, "site1", 200.0, 10.0),
    ]
    _write_bronze_eeg(spark, bronze, rows)
    build_eeg_silver(spark, bronze, silver)
    out = spark.read.parquet(f"{silver}/eeg")
    assert out.count() == 2


@pytest.mark.skipif(not _HAS_PYSPARK, reason="pyspark not installed")
def test_ehr_silver_keeps_latest_version_per_encounter(spark, tmp_path):
    from brainwatch.processing.silver_layer import build_ehr_silver
    from datetime import datetime

    bronze = str(tmp_path / "bronze")
    silver = str(tmp_path / "silver")
    t = datetime(2026, 5, 19, 12, 0, 0)
    rows = [
        ("p1", "enc1", t, "vital_signs", 1),
        ("p1", "enc1", t, "vital_signs", 2),
        ("p1", "enc1", t, "vital_signs", 3),
        ("p2", "enc2", t, "lab_result", 1),
    ]
    _write_bronze_ehr(spark, bronze, rows)
    build_ehr_silver(spark, bronze, silver)
    out = spark.read.parquet(f"{silver}/ehr").collect()
    by_enc = {(r["patient_id"], r["encounter_id"]): r["version"] for r in out}
    assert by_enc[("p1", "enc1")] == 3
    assert by_enc[("p2", "enc2")] == 1


@pytest.mark.skipif(not _HAS_PYSPARK, reason="pyspark not installed")
def test_quality_flag_marks_low_sampling_rate(spark, tmp_path):
    from brainwatch.processing.silver_layer import build_eeg_silver
    from datetime import datetime

    bronze = str(tmp_path / "bronze")
    silver = str(tmp_path / "silver")
    t = datetime(2026, 5, 19, 10, 0, 0)
    rows = [
        ("p_low", "s1", t, "site1", 50.0, 10.0),     # LOW_SR
        ("p_short", "s2", t, "site1", 200.0, 2.0),   # SHORT_WINDOW
        ("p_ok", "s3", t, "site1", 200.0, 10.0),     # OK
    ]
    _write_bronze_eeg(spark, bronze, rows)
    build_eeg_silver(spark, bronze, silver)
    flags = {r["patient_id"]: r["quality_flag"] for r in spark.read.parquet(f"{silver}/eeg").collect()}
    assert flags["p_low"] == "LOW_SR"
    assert flags["p_short"] == "SHORT_WINDOW"
    assert flags["p_ok"] == "OK"

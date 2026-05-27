"""Bronze-layer batch processing — raw ingestion into the data lake.

Reads raw JSONL/Parquet EEG and EHR events, validates against contracts,
performs SHA-256 deduplication, and writes bronze Parquet partitioned by
``(site_id, date)``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from brainwatch.contracts.events import (
    BronzeEEGRecord,
    BronzeEHRRecord,
    EEGChunkEvent,
    EHREvent,
    compute_fingerprint,
)
from brainwatch.contracts.validators import validate_eeg_event, validate_ehr_event

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure-Python bronze loader (no Spark required)
# ---------------------------------------------------------------------------

def load_eeg_jsonl(jsonl_path: str | Path) -> list[dict[str, Any]]:
    """Load EEG events from a JSONL file and return bronze records."""
    records: list[dict[str, Any]] = []
    seen_fingerprints: set[str] = set()
    errors: list[dict[str, Any]] = []

    with Path(jsonl_path).open("r", encoding="utf-8") as fh:
        for line_num, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                # Handle wrapped format {key, value}
                if "value" in data and isinstance(data["value"], dict):
                    data = data["value"]

                validation_errors = validate_eeg_event(data)
                if validation_errors:
                    errors.append({"line": line_num, "errors": validation_errors, "data": data})
                    continue

                event = EEGChunkEvent.from_dict(data)
                if event.fingerprint in seen_fingerprints:
                    continue  # dedup
                seen_fingerprints.add(event.fingerprint)

                bronze = BronzeEEGRecord.from_eeg_event(event)
                records.append(bronze.to_dict())
            except Exception as exc:
                errors.append({"line": line_num, "error": str(exc)})

    logger.info("Bronze EEG: %d records loaded, %d errors, %d deduped",
                len(records), len(errors), len(seen_fingerprints) - len(records))
    return records


def load_ehr_jsonl(jsonl_path: str | Path) -> list[dict[str, Any]]:
    """Load EHR events from a JSONL file and return bronze records."""
    records: list[dict[str, Any]] = []
    seen_fingerprints: set[str] = set()
    errors: list[dict[str, Any]] = []

    with Path(jsonl_path).open("r", encoding="utf-8") as fh:
        for line_num, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if "value" in data and isinstance(data["value"], dict):
                    data = data["value"]

                validation_errors = validate_ehr_event(data)
                if validation_errors:
                    errors.append({"line": line_num, "errors": validation_errors, "data": data})
                    continue

                event = EHREvent.from_dict(data)
                if event.fingerprint in seen_fingerprints:
                    continue
                seen_fingerprints.add(event.fingerprint)

                bronze = BronzeEHRRecord.from_ehr_event(event)
                records.append(bronze.to_dict())
            except Exception as exc:
                errors.append({"line": line_num, "error": str(exc)})

    logger.info("Bronze EHR: %d records loaded, %d errors", len(records), len(errors))
    return records


def write_bronze_parquet(records: list[dict[str, Any]], output_dir: str | Path, table_name: str = "eeg") -> str:
    """Write bronze records to Parquet using Spark.

    Falls back to JSON if Spark is unavailable.
    """
    output_path = Path(output_dir) / table_name
    output_path.mkdir(parents=True, exist_ok=True)

    try:
        return _write_with_spark(records, str(output_path))
    except ImportError:
        return _write_as_json(records, output_path)


def _write_with_spark(records: list[dict[str, Any]], output_path: str) -> str:
    """Write records as partitioned Parquet using PySpark."""
    from brainwatch.processing.spark_helpers import get_or_create_spark_session

    spark = get_or_create_spark_session(app_name="BrainWatch-BronzeLoader")
    df = spark.createDataFrame(records)

    if "site_id" in df.columns and "date" in df.columns:
        df.write.mode("append").partitionBy("site_id", "date").parquet(output_path)
    else:
        df.write.mode("append").parquet(output_path)

    logger.info("Bronze Parquet written to %s (%d records)", output_path, len(records))
    return output_path


def _write_as_json(records: list[dict[str, Any]], output_path: Path) -> str:
    """Fallback: write records as JSONL when Spark is unavailable."""
    jsonl_path = output_path / "bronze.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, default=str) + "\n")
    logger.info("Bronze JSON fallback written to %s (%d records)", jsonl_path, len(records))
    return str(jsonl_path)


# ---------------------------------------------------------------------------
# Spark-native bronze loader
# ---------------------------------------------------------------------------

def spark_bronze_load(
    spark: Any,
    input_path: str,
    output_path: str,
    input_format: str = "json",
    partition_columns: list[str] | None = None,
) -> Any:
    """Load raw events into bronze Parquet using a Spark DataFrame pipeline.

    Parameters
    ----------
    spark : SparkSession
    input_path : path to raw JSONL or Parquet
    output_path : bronze output directory
    input_format : ``"json"`` or ``"parquet"``
    partition_columns : columns to partition by (default: site_id, date)

    Returns
    -------
    The written DataFrame (for chaining).
    """
    from pyspark.sql import functions as F

    if partition_columns is None:
        partition_columns = ["site_id", "date"]

    df = spark.read.format(input_format).load(input_path)

    # Add ingestion metadata
    df = df.withColumn("ingestion_time", F.current_timestamp())

    # Add date partition column if not present
    if "date" not in df.columns and "event_time" in df.columns:
        df = df.withColumn("date", F.to_date(F.col("event_time")))

    # Deduplicate by fingerprint
    if "fingerprint" in df.columns:
        df = df.dropDuplicates(["fingerprint"])

    # Write
    writer = df.write.mode("append").format("parquet")
    if partition_columns:
        existing_cols = [c for c in partition_columns if c in df.columns]
        if existing_cols:
            writer = writer.partitionBy(*existing_cols)
    writer.save(output_path)

    logger.info("Spark bronze load: %s → %s", input_path, output_path)
    return df

"""Spark Structured Streaming consumer: Kafka → Bronze zone.

Owner: **Quang-Hung** (lead).
Depends on: ``brainwatch.contracts.events`` schemas and pyspark 3.5.

Hard contract: every PySpark import in this file MUST happen inside the
function bodies. The module has to be importable without ``pyspark``
installed — that's how the test suite stays green for everyone else.
"""
from __future__ import annotations

from typing import Any


def _eeg_schema():
    """Quang-Hung: build the StructType matching ``EEGChunkEvent``.

    Required columns: patient_id, session_id, event_time (Timestamp), site_id.
    Optional: channel_count, sampling_rate_hz, window_seconds, source_uri.
    """
    # Quang-Hung: import pyspark types and build the schema here.
    pass


def _ehr_schema():
    """Quang-Hung: same idea for ``EHREvent``.
    Required: patient_id, encounter_id, event_time, event_type.
    Optional: source_system, version.
    (We deliberately don't model ``payload`` in the schema — leave it as
    a JSON string column for now; flatten in the Silver layer in Week 3.)
    """
    # Quang-Hung: build the EHR schema here.
    pass


# ---------------------------------------------------------------------------
# Bronze ingestion queries
# ---------------------------------------------------------------------------

def build_eeg_bronze_query(
    spark: Any,
    kafka_servers: str,
    eeg_topic: str,
    bronze_path: str,
    checkpoint_path: str,
    dead_letter_path: str | None = None,
):
    """Structured Streaming: Kafka ``eeg.raw`` → Bronze Parquet.

    Quang-Hung: implement.
      1. ``spark.readStream.format("kafka")`` with subscribe + earliest offsets,
         ``failOnDataLoss=false`` (we tolerate retention).
      2. parse ``value`` with ``from_json`` against ``_eeg_schema()``.
      3. keep ``kafka_ts``, ``kafka_partition``, ``kafka_offset`` for lineage.
      4. valid rows = ``patient_id IS NOT NULL AND session_id IS NOT NULL``.
      5. add ``ingestion_time = current_timestamp()`` and
         ``ingestion_date = to_date(ingestion_time)``.
      6. write Parquet partitioned by ``site_id, ingestion_date``,
         checkpoint at ``{checkpoint_path}/eeg_bronze``,
         ``processingTime="30 seconds"``.
      7. if ``dead_letter_path``: build a parallel writeStream of the invalid
         rows as JSON to ``{dead_letter_path}/eeg``.
      8. return ``(query, invalid_query_or_None)``.
    """
    # Quang-Hung: code the EEG bronze query here.
    pass


def build_ehr_bronze_query(
    spark: Any,
    kafka_servers: str,
    ehr_topic: str,
    bronze_path: str,
    checkpoint_path: str,
):
    """Structured Streaming: Kafka ``ehr.updates`` → Bronze Parquet.

    Quang-Hung: implement. Same shape as the EEG query, but:
      - watermark on ``event_time`` with ``"30 minutes"`` tolerance.
      - partition by ``ingestion_date`` only (no site_id on EHR).
      - no DLQ branch — EHR validation is lighter (Week 2).
    """
    # Quang-Hung: code the EHR bronze query here.
    pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def start_bronze_ingestion(
    spark: Any,
    kafka_servers: str = "kafka:9092",
    eeg_topic: str = "eeg.raw",
    ehr_topic: str = "ehr.updates",
    bronze_path: str = "data/lake/bronze",
    checkpoint_path: str = "data/checkpoints",
    dead_letter_path: str = "data/lake/dead_letter",
):
    """Wire up both EEG and EHR bronze ingestion streams and ``.start()`` each.

    Quang-Hung: implement.
      - call build_eeg_bronze_query(...) → (eeg_q, eeg_dlq_q)
      - call build_ehr_bronze_query(...) → ehr_q
      - .start() each, return list of StreamingQuery objects.
      - print a one-line summary so the operator knows it's alive.
    """
    # Quang-Hung: code the start_bronze_ingestion entry point here.
    pass

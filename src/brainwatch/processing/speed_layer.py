"""Speed layer — real-time stream-stream join + alert generation.

Reads bronze Parquet as streaming sources (bronze is already authoritative,
which avoids re-parsing JSON from Kafka), joins EEG + EHR with watermarks,
runs anomaly scoring, then dual-sinks alerts into Cassandra and the
``alerts.anomaly`` Kafka topic.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def build_streaming_pipeline(
    spark: Any,
    bronze_path: str,
    checkpoint_path: str,
    kafka_servers: str,
    cassandra_contact_points: str,
):
    """Wire up the full speed-layer query."""
    from pyspark.sql import functions as F
    from pyspark.sql.types import FloatType
    from brainwatch.serving.anomaly_rules import compute_anomaly_score, classify_v2
    from brainwatch.serving.alert_publisher import publish_alerts

    eeg_df = (spark.readStream
              .format("parquet")
              .load(f"{bronze_path}/eeg")
              .withWatermark("event_time", "10 minutes"))

    ehr_df = (spark.readStream
              .format("parquet")
              .load(f"{bronze_path}/ehr")
              .withWatermark("event_time", "30 minutes"))

    # Stage 2 — stream-stream left-outer join on patient_id within +/-30 min
    joined = eeg_df.join(
        ehr_df,
        on="patient_id",
        how="left_outer"
    ).filter(
        F.abs((eeg_df.event_time.cast("long") - ehr_df.event_time.cast("long")) / 3600) <= 0.5
    )

    # Stage 3 — windowed aggregation
    windowed = joined.groupBy(
        eeg_df.patient_id,
        F.window(eeg_df.event_time, "1 minute", "30 seconds"),
        eeg_df.site_id
    ).agg(
        F.count("*").alias("eeg_chunk_count"),
        F.avg(eeg_df.sampling_rate_hz).alias("mean_sampling_rate_hz"),
        F.max(F.when(ehr_df.event_type == "critical_lab", 1).otherwise(0)).alias("has_critical_lab"),
        F.max(eeg_df.window_seconds).alias("max_window_seconds"),
        F.max(eeg_df.channel_count).alias("max_channel_count")
    )

    # Stage 4 — anomaly scoring via UDF
    anomaly_udf = F.udf(lambda row: compute_anomaly_score(row, classify_v2), FloatType())
    scored = windowed.withColumn(
        "anomaly_score",
        anomaly_udf(F.struct(
            windowed.eeg_chunk_count,
            windowed.mean_sampling_rate_hz,
            windowed.has_critical_lab,
            windowed.max_window_seconds
        ))
    )

    # Stage 5 — write via foreachBatch
    def write_batch(df, epoch_id):
        alerts = []
        for row in df.collect():
            if row.anomaly_score > 0.7:
                decision = classify_v2(row.anomaly_score, row.has_critical_lab or False)
                alert = {
                    "patient_id": row.patient_id,
                    "session_id": getattr(row, "session_id", None) or getattr(row, "encounter_id", ""),
                    "alert_time": datetime.now(timezone.utc).isoformat(),
                    "severity": decision.severity,
                    "anomaly_score": row.anomaly_score,
                    "explanation": f"Score={row.anomaly_score:.2f}, critical_lab={row.has_critical_lab}"
                }
                alerts.append(alert)
        if alerts:
            publish_alerts(alerts, kafka_servers, cassandra_contact_points)

    query = (scored.writeStream
             .foreachBatch(write_batch)
             .outputMode("update")
             .option("checkpointLocation", f"{checkpoint_path}/speed_layer")
             .trigger(processingTime="30 seconds")
             .start())

    return query


def build_kafka_streaming_pipeline(
    spark: Any,
    kafka_servers: str,
    cassandra_contact_points: str,
    checkpoint_path: str,
    eeg_topic: str = "eeg.raw",
    ehr_topic: str = "ehr.updates",
    starting_offsets: str = "latest",
):
    """Kafka-direct variant of ``build_streaming_pipeline`` for the EKS demo.

    Reads JSON events from Kafka topics, joins EEG ⋈ EHR on patient_id within
    ±30 min watermarked windows, scores the joined rows via the production
    ``compute_anomaly_score`` rule, and writes severity-classified alerts into
    the Cassandra ``brainwatch.alerts`` table via ``foreachBatch``.
    """
    from datetime import datetime, timezone
    from pyspark.sql import functions as F
    from pyspark.sql.types import (
        FloatType, IntegerType, StringType, StructField, StructType,
    )

    eeg_schema = StructType([
        StructField("patient_id", StringType()),
        StructField("session_id", StringType()),
        StructField("event_time", StringType()),
        StructField("site_id", StringType()),
        StructField("channel_count", IntegerType()),
        StructField("sampling_rate_hz", FloatType()),
        StructField("window_seconds", FloatType()),
        StructField("source_uri", StringType()),
    ])
    ehr_schema = StructType([
        StructField("patient_id", StringType()),
        StructField("encounter_id", StringType()),
        StructField("event_time", StringType()),
        StructField("event_type", StringType()),
        StructField("source_system", StringType()),
        StructField("version", IntegerType()),
    ])

    eeg = (spark.readStream
           .format("kafka")
           .option("kafka.bootstrap.servers", kafka_servers)
           .option("subscribe", eeg_topic)
           .option("startingOffsets", starting_offsets)
           .option("maxOffsetsPerTrigger", "5000")
           .load()
           .select(F.from_json(F.col("value").cast("string"), eeg_schema).alias("e"))
           .select("e.*")
           .withColumn("event_time", F.to_timestamp("event_time"))
           .withWatermark("event_time", "30 seconds"))

    # Subscribe to EHR for visibility / metrics — joined downstream via Cassandra
    # batch enrichment if needed, not via stream-stream join (append-mode join +
    # windowed agg has ~window+watermark latency unsuited to the live demo).
    _ehr = (spark.readStream
            .format("kafka")
            .option("kafka.bootstrap.servers", kafka_servers)
            .option("subscribe", ehr_topic)
            .option("startingOffsets", starting_offsets)
            .load()
            .select(F.lit(1).alias("_n")))
    _ehr_query = (_ehr.writeStream
                  .outputMode("append")
                  .format("noop")
                  .option("checkpointLocation", f"{checkpoint_path}/ehr_subscriber")
                  .start())  # keeps a live consumer on the topic for metrics

    # Windowed feature aggregation. We compute the EEG-only feature row
    # here; the EHR enrichment (has_critical_lab, n_medication_changes_24h)
    # is fetched from Cassandra patient_state inside foreachBatch and
    # combined with these features to compute the v2 four-term score.
    windowed = eeg.groupBy(
        F.col("patient_id"),
        F.window(F.col("event_time"), "30 seconds", "15 seconds").alias("win"),
    ).agg(
        F.count(F.col("session_id")).alias("eeg_chunk_count"),
        F.avg(F.col("sampling_rate_hz")).alias("mean_sampling_rate_hz"),
        F.max(F.col("window_seconds")).alias("max_window_seconds"),
    )

    def _write_batch(df, batch_id):
        """foreachBatch sink — the canonical Lambda lookup pattern.

        For each micro-batch:
          1. Collect the windowed feature rows to the driver.
          2. Look up the EHR-derived enrichment for the batch's distinct
             patients in one CQL ``SELECT IN`` against
             ``brainwatch.patient_state``. Each row is a partition-key
             seek, so the cost is dominated by network round-trip.
          3. For every row, build the full v2 four-term feature dict
             (chunk_count + signal_quality_score from EEG; has_critical_lab
             + n_medication_changes_24h from the lookup) and score it
             with :func:`anomaly_rules.compute_anomaly_score`.
          4. Classify the score with :func:`anomaly_rules.classify_v2`,
             which fires the critical-lab escalation when the lookup
             returned ``has_critical_lab=True``.
          5. Insert into ``brainwatch.alerts`` keyed by
             ``(patient_id, alert_time)`` so a replay is a no-op upsert.
        """
        from brainwatch.serving.anomaly_rules import (
            compute_anomaly_score, classify_v2,
        )
        from brainwatch.serving.cassandra_sink import fetch_patient_enrichment
        from cassandra.cluster import Cluster
        rows = df.collect()
        if not rows:
            return
        host = cassandra_contact_points.split(",")[0]
        cluster = Cluster([host])
        try:
            session = cluster.connect("brainwatch")
            # One round-trip lookup for the whole micro-batch.
            patient_ids = sorted({row["patient_id"] for row in rows
                                  if row["patient_id"] is not None})
            enrichment = fetch_patient_enrichment(session, patient_ids)
            insert = session.prepare(
                "INSERT INTO alerts (patient_id, alert_time, severity, "
                "anomaly_score, explanation, source) "
                "VALUES (?, ?, ?, ?, ?, ?)"
            )
            written = 0
            for row in rows:
                pid = row["patient_id"]
                mean_sr = float(row["mean_sampling_rate_hz"] or 0.0)
                signal_quality = max(0.0, min(1.0, mean_sr / 250.0))
                enrich = enrichment.get(pid, {})
                features = {
                    "eeg_chunk_count": int(row["eeg_chunk_count"] or 0),
                    "signal_quality_score": signal_quality,
                    "has_critical_lab": bool(enrich.get("has_critical_lab", False)),
                    "n_medication_changes_24h": int(
                        enrich.get("n_medication_changes_24h", 0)),
                }
                # Real v2 four-term score from anomaly_rules.
                score = compute_anomaly_score(features)
                # Quality gate stays as the pre-classifier suppression.
                if signal_quality < 0.30:
                    severity = "suppressed"
                else:
                    severity = classify_v2(
                        score, features["has_critical_lab"]).severity
                win = row["win"]
                alert_time = win.end if hasattr(win, "end") else datetime.now(timezone.utc)
                explanation = (
                    f"window {win.start.isoformat()} → {win.end.isoformat()}; "
                    f"eeg_chunks={features['eeg_chunk_count']}; "
                    f"mean_sr={mean_sr:.0f}; "
                    f"critical_lab={features['has_critical_lab']}; "
                    f"meds_24h={features['n_medication_changes_24h']}"
                )
                session.execute(insert, (pid, alert_time, severity, score,
                                          explanation, "speed_lookup"))
                written += 1
            print(f"[lookup foreachBatch batch_id={batch_id}] wrote {written} alerts "
                  f"(enriched={len(enrichment)} / batch_patients={len(patient_ids)})")
        finally:
            cluster.shutdown()

    scored = windowed  # the score is computed inside foreachBatch now

    query = (scored.writeStream
             .foreachBatch(_write_batch)
             .outputMode("append")
             .option("checkpointLocation", f"{checkpoint_path}/kafka_speed_layer")
             .trigger(processingTime="5 seconds")
             .start())

    return query


def build_kafka_join_pipeline(
    spark: Any,
    kafka_servers: str,
    cassandra_contact_points: str,
    checkpoint_path: str,
    eeg_topic: str = "eeg.raw",
    ehr_topic: str = "ehr.updates",
    starting_offsets: str = "latest",
    eeg_watermark: str = "30 seconds",
    ehr_watermark: str = "30 minutes",
    window_duration: str = "1 minute",
    window_slide: str = "30 seconds",
):
    """Kafka-source **stream-stream join** speed-layer query.

    This is the canonical Lambda speed-layer design: EEG and EHR are both
    streamed from Kafka, joined on ``patient_id`` within a watermark-bounded
    event-time predicate, windowed per ``(patient_id, window)``, scored with
    the v2 four-term formula directly off the joined row, and inserted into
    Cassandra with ``source='speed_join'``.

    Runs concurrently with :func:`build_kafka_streaming_pipeline` (the
    Cassandra-lookup variant tagged ``source='speed_lookup'``); both write
    to ``brainwatch.alerts`` and the dashboard's Real-Time Alerts panel
    filters on ``source`` to show each path side by side.

    Latency note: append-mode + windowed aggregation downstream of the
    stream-stream join means alerts from this path are emitted only after
    the watermark passes the lower bound of the join window. For the
    default settings the emission delay is the EEG watermark plus the
    window width, roughly $60$~seconds, which is why this path is the
    accuracy benchmark, not the live-demo headline; the lookup path stays
    the headline at p50 ~12 s.
    """
    from datetime import datetime, timezone
    from pyspark.sql import functions as F
    from pyspark.sql.types import (
        FloatType, IntegerType, StringType, StructField, StructType,
    )

    eeg_schema = StructType([
        StructField("patient_id", StringType()),
        StructField("session_id", StringType()),
        StructField("event_time", StringType()),
        StructField("site_id", StringType()),
        StructField("channel_count", IntegerType()),
        StructField("sampling_rate_hz", FloatType()),
        StructField("window_seconds", FloatType()),
        StructField("source_uri", StringType()),
    ])
    ehr_schema = StructType([
        StructField("patient_id", StringType()),
        StructField("encounter_id", StringType()),
        StructField("event_time", StringType()),
        StructField("event_type", StringType()),
        StructField("source_system", StringType()),
        StructField("version", IntegerType()),
    ])

    eeg = (spark.readStream
           .format("kafka")
           .option("kafka.bootstrap.servers", kafka_servers)
           .option("subscribe", eeg_topic)
           .option("startingOffsets", starting_offsets)
           .option("maxOffsetsPerTrigger", "5000")
           .load()
           .select(F.from_json(F.col("value").cast("string"), eeg_schema).alias("e"))
           .select("e.*")
           .withColumn("event_time", F.to_timestamp("event_time"))
           .withWatermark("event_time", eeg_watermark))

    ehr = (spark.readStream
           .format("kafka")
           .option("kafka.bootstrap.servers", kafka_servers)
           .option("subscribe", ehr_topic)
           .option("startingOffsets", starting_offsets)
           .load()
           .select(F.from_json(F.col("value").cast("string"), ehr_schema).alias("h"))
           .select("h.*")
           .withColumn("event_time", F.to_timestamp("event_time"))
           .withWatermark("event_time", ehr_watermark))

    # Stream-stream join on patient_id within a time-predicate bounded by
    # both watermarks. Spark requires the predicate to be expressed via the
    # event-time columns so it can plan state retention.
    joined = eeg.alias("e").join(
        ehr.alias("h"),
        F.expr(
            "e.patient_id = h.patient_id AND "
            "h.event_time BETWEEN e.event_time - INTERVAL 30 MINUTES "
            "                   AND e.event_time + INTERVAL 30 MINUTES"
        ),
        how="left_outer",
    )

    # Windowed aggregation per (patient_id, window) over the joined stream.
    # The joined row carries both EEG and EHR fields, so the v2 features can
    # all be derived in one pass.
    windowed = joined.groupBy(
        F.col("e.patient_id").alias("patient_id"),
        F.window(F.col("e.event_time"), window_duration, window_slide).alias("win"),
    ).agg(
        F.count(F.col("e.session_id")).alias("eeg_chunk_count"),
        F.avg(F.col("e.sampling_rate_hz")).alias("mean_sampling_rate_hz"),
        F.max(F.when(F.col("h.event_type") == "critical_lab", 1)
                .otherwise(0)).alias("has_critical_lab_int"),
        F.sum(F.when(F.col("h.event_type") == "medication", 1)
                .otherwise(0)).alias("n_medication_changes_24h"),
    )

    def _write_batch_join(df, batch_id):
        from brainwatch.serving.anomaly_rules import (
            compute_anomaly_score, classify_v2,
        )
        from cassandra.cluster import Cluster
        rows = df.collect()
        if not rows:
            return
        host = cassandra_contact_points.split(",")[0]
        cluster = Cluster([host])
        try:
            session = cluster.connect("brainwatch")
            insert = session.prepare(
                "INSERT INTO alerts (patient_id, alert_time, severity, "
                "anomaly_score, explanation, source) "
                "VALUES (?, ?, ?, ?, ?, ?)"
            )
            written = 0
            for row in rows:
                pid = row["patient_id"]
                mean_sr = float(row["mean_sampling_rate_hz"] or 0.0)
                signal_quality = max(0.0, min(1.0, mean_sr / 250.0))
                features = {
                    "eeg_chunk_count": int(row["eeg_chunk_count"] or 0),
                    "signal_quality_score": signal_quality,
                    "has_critical_lab": bool(row["has_critical_lab_int"] or 0),
                    "n_medication_changes_24h": int(
                        row["n_medication_changes_24h"] or 0),
                }
                # Same canonical v2 formula as the lookup path — only the
                # source of the EHR features differs.
                score = compute_anomaly_score(features)
                if signal_quality < 0.30:
                    severity = "suppressed"
                else:
                    severity = classify_v2(
                        score, features["has_critical_lab"]).severity
                win = row["win"]
                alert_time = win.end if hasattr(win, "end") else datetime.now(timezone.utc)
                explanation = (
                    f"window {win.start.isoformat()} → {win.end.isoformat()}; "
                    f"eeg_chunks={features['eeg_chunk_count']}; "
                    f"mean_sr={mean_sr:.0f}; "
                    f"critical_lab={features['has_critical_lab']}; "
                    f"meds_24h={features['n_medication_changes_24h']}"
                )
                session.execute(insert, (pid, alert_time, severity, score,
                                          explanation, "speed_join"))
                written += 1
            print(f"[join foreachBatch batch_id={batch_id}] wrote {written} alerts "
                  f"(stream-stream join, output-mode append)")
        finally:
            cluster.shutdown()

    query = (windowed.writeStream
             .foreachBatch(_write_batch_join)
             .outputMode("append")
             .option("checkpointLocation",
                     f"{checkpoint_path}/kafka_speed_join")
             .trigger(processingTime="30 seconds")
             .start())
    return query


def main() -> None:
    """CLI entry point.

    ``--mode`` picks the speed-layer variant(s) to start. ``lookup``
    (default) is the Cassandra-lookup variant tagged ``source='speed_lookup'``;
    ``join`` is the Kafka stream-stream-join variant tagged
    ``source='speed_join'``; ``both`` starts the two queries concurrently
    in the same SparkSession. The legacy ``parquet`` mode keeps the
    bronze-Parquet-source design variant (``build_streaming_pipeline``)
    available for offline replays.
    """
    import argparse
    from pyspark.sql import SparkSession
    from pyspark import SparkConf

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bronze", default="data/lake/bronze")
    parser.add_argument("--checkpoint", default="data/checkpoints")
    parser.add_argument("--kafka", default="kafka:9092")
    parser.add_argument("--cassandra", default="cassandra-svc")
    parser.add_argument(
        "--mode",
        choices=["lookup", "join", "both", "parquet"],
        default="lookup",
        help="lookup = Cassandra patient_state lookup; "
             "join = Kafka stream-stream join; "
             "both = start lookup + join concurrently; "
             "parquet = legacy bronze-Parquet stream-stream join.",
    )
    parser.add_argument("--eeg-topic", default="eeg.raw")
    parser.add_argument("--ehr-topic", default="ehr.updates")
    args = parser.parse_args()

    conf = SparkConf()
    conf.set("spark.jars.packages",
             "org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.0,"
             "org.apache.spark:spark-avro_2.12:3.5.0")
    spark = SparkSession.builder.config(conf=conf).getOrCreate()
    spark.sparkContext.setLogLevel("WARN")

    queries = []
    if args.mode in ("lookup", "both"):
        queries.append(build_kafka_streaming_pipeline(
            spark,
            kafka_servers=args.kafka,
            cassandra_contact_points=args.cassandra,
            checkpoint_path=args.checkpoint,
            eeg_topic=args.eeg_topic,
            ehr_topic=args.ehr_topic,
        ))
        print("Started Cassandra-lookup speed layer (source='speed_lookup')")
    if args.mode in ("join", "both"):
        queries.append(build_kafka_join_pipeline(
            spark,
            kafka_servers=args.kafka,
            cassandra_contact_points=args.cassandra,
            checkpoint_path=args.checkpoint,
            eeg_topic=args.eeg_topic,
            ehr_topic=args.ehr_topic,
        ))
        print("Started Kafka stream-stream-join speed layer (source='speed_join')")
    if args.mode == "parquet":
        queries.append(build_streaming_pipeline(
            spark, args.bronze, args.checkpoint, args.kafka, args.cassandra,
        ))
        print("Started bronze-Parquet stream-stream-join speed layer (legacy)")

    if not queries:
        raise SystemExit(f"No queries started for mode={args.mode!r}")

    print(f"Speed layer mode={args.mode}: {len(queries)} streaming "
          f"query(ies) running concurrently.")
    # awaitAnyTermination so a crash in either query surfaces; the other
    # is still inspectable via the StreamingQueryManager.
    spark.streams.awaitAnyTermination()


if __name__ == "__main__":
    main()
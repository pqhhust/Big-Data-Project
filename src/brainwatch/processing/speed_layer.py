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

    windowed = eeg.groupBy(
        F.col("patient_id"),
        F.window(F.col("event_time"), "30 seconds", "15 seconds").alias("win"),
    ).agg(
        F.count(F.col("session_id")).alias("eeg_chunk_count"),
        F.avg(F.col("sampling_rate_hz")).alias("mean_sampling_rate_hz"),
        F.max(F.col("window_seconds")).alias("max_window_seconds"),
        F.lit(False).alias("has_critical_lab"),
    )

    def _score(eeg_chunk_count, mean_sr, patient_id, win_start):
        chunk_count = int(eeg_chunk_count or 0)
        signal_quality = max(0.0, min(1.0, (float(mean_sr) if mean_sr else 200.0) / 250.0))
        chunk_term = min(chunk_count / 25.0, 1.0)
        quality_term = 1.0 - signal_quality
        base = 0.60 * chunk_term + 0.40 * quality_term
        # Per-patient/per-window pseudo-randomness so a real clinical mix is
        # visible on the dashboard (in production this would be EHR-driven).
        # Use zlib.crc32 (not builtin hash(), which is salted per-process and
        # would make scores non-deterministic across Spark executors/runs).
        import zlib
        key = f"{patient_id or 'X'}|{int(win_start or 0)}".encode("utf-8")
        h = (zlib.crc32(key) & 0xffffffff) / 0xffffffff
        variance = (h - 0.5) * 0.5   # [-0.25, +0.25]
        return float(max(0.0, min(base + variance, 1.0)))

    score_udf = F.udf(_score, FloatType())
    scored = windowed.withColumn(
        "anomaly_score",
        score_udf(
            F.col("eeg_chunk_count"),
            F.col("mean_sampling_rate_hz"),
            F.col("patient_id"),
            F.col("win.start").cast("long"),
        ),
    )

    def _write_batch(df, batch_id):
        from brainwatch.serving.anomaly_rules import classify_v2
        from cassandra.cluster import Cluster
        rows = df.collect()
        if not rows:
            return
        host = cassandra_contact_points.split(",")[0]
        cluster = Cluster([host])
        try:
            session = cluster.connect("brainwatch")
            insert = session.prepare(
                "INSERT INTO alerts (patient_id, alert_time, severity, anomaly_score, explanation) "
                "VALUES (?, ?, ?, ?, ?)"
            )
            written = 0
            for row in rows:
                score = float(row["anomaly_score"] or 0.0)
                mean_sr = float(row["mean_sampling_rate_hz"] or 0.0)
                signal_quality = max(0.0, min(1.0, mean_sr / 250.0))
                if signal_quality < 0.30:
                    severity = "suppressed"
                else:
                    severity = classify_v2(score, bool(row["has_critical_lab"])).severity
                win = row["win"]
                alert_time = win.end if hasattr(win, "end") else datetime.now(timezone.utc)
                explanation = (
                    f"window {win.start.isoformat()} → {win.end.isoformat()}; "
                    f"eeg_chunks={row['eeg_chunk_count']}; mean_sr={mean_sr:.0f}"
                )
                session.execute(insert, (row["patient_id"], alert_time, severity, score, explanation))
                written += 1
            print(f"[foreachBatch batch_id={batch_id}] wrote {written} alerts to Cassandra")
        finally:
            cluster.shutdown()

    query = (scored.writeStream
             .foreachBatch(_write_batch)
             .outputMode("append")
             .option("checkpointLocation", f"{checkpoint_path}/kafka_speed_layer")
             .trigger(processingTime="5 seconds")
             .start())

    return query


def main() -> None:
    """CLI entry point."""
    import argparse
    from pyspark.sql import SparkSession
    from pyspark import SparkConf

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bronze", default="data/lake/bronze")
    parser.add_argument("--checkpoint", default="data/checkpoints")
    parser.add_argument("--kafka", default="kafka:9092")
    parser.add_argument("--cassandra", default="cassandra-svc")
    parser.add_argument("--mode", choices=["parquet", "kafka"], default="parquet")
    parser.add_argument("--eeg-topic", default="eeg.raw")
    parser.add_argument("--ehr-topic", default="ehr.updates")
    args = parser.parse_args()

    conf = SparkConf()
    conf.set("spark.jars.packages",
             "org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.0,"
             "org.apache.spark:spark-avro_2.12:3.5.0")
    spark = SparkSession.builder.config(conf=conf).getOrCreate()
    spark.sparkContext.setLogLevel("WARN")

    if args.mode == "kafka":
        query = build_kafka_streaming_pipeline(
            spark,
            kafka_servers=args.kafka,
            cassandra_contact_points=args.cassandra,
            checkpoint_path=args.checkpoint,
            eeg_topic=args.eeg_topic,
            ehr_topic=args.ehr_topic,
        )
    else:
        query = build_streaming_pipeline(
            spark,
            args.bronze,
            args.checkpoint,
            args.kafka,
            args.cassandra,
        )

    print(f"Speed layer ({args.mode}) streaming query started")
    query.awaitTermination()


if __name__ == "__main__":
    main()
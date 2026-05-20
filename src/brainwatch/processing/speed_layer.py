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
                severity = classify_v2(row.anomaly_score, row.has_critical_lab or False)
                alert = {
                    "patient_id": row.patient_id,
                    "session_id": row.session_id if hasattr(row, 'session_id') else row.encounter_id,
                    "alert_time": datetime.now(timezone.utc).isoformat(),
                    "severity": severity,
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
    args = parser.parse_args()

    # Build SparkSession with Kafka package
    conf = SparkConf()
    conf.set("spark.jars.packages",
             "org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.0,"
             "org.apache.spark:spark-avro_2.12:3.5.0")
    spark = SparkSession.builder.config(conf=conf).getOrCreate()

    query = build_streaming_pipeline(
        spark,
        args.bronze,
        args.checkpoint,
        args.kafka,
        args.cassandra
    )

    print("Speed layer streaming query started")
    query.awaitTermination()


if __name__ == "__main__":
    main()
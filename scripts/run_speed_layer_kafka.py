#!/usr/bin/env python3
"""Kubernetes/EKS entrypoint for the Kafka-source speed layer.

Designed to be invoked by ``spark-submit`` from within the long-running
``speed-layer`` Deployment on the brainwatch namespace.
"""
from __future__ import annotations

import os
import sys


def main() -> int:
    from pyspark.sql import SparkSession
    from brainwatch.processing.speed_layer import build_kafka_streaming_pipeline

    kafka_servers = os.environ.get(
        "KAFKA_BOOTSTRAP",
        "kafka-0.kafka.brainwatch.svc.cluster.local:9092",
    )
    cassandra_host = os.environ.get(
        "CASSANDRA_HOST",
        "cassandra-0.cassandra-svc.brainwatch.svc.cluster.local",
    )
    checkpoint_path = os.environ.get("CHECKPOINT_PATH", "/data/checkpoints")
    eeg_topic = os.environ.get("EEG_TOPIC", "eeg.raw")
    ehr_topic = os.environ.get("EHR_TOPIC", "ehr.updates")

    spark = (
        SparkSession.builder
        .appName("brainwatch-kafka-speed-layer")
        .config("spark.sql.shuffle.partitions", "8")
        .config("spark.sql.streaming.minBatchesToRetain", "5")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("WARN")

    print(f"[speed_layer] kafka={kafka_servers}  cassandra={cassandra_host}", flush=True)
    print(f"[speed_layer] topics=({eeg_topic}, {ehr_topic})  checkpoint={checkpoint_path}", flush=True)

    query = build_kafka_streaming_pipeline(
        spark,
        kafka_servers=kafka_servers,
        cassandra_contact_points=cassandra_host,
        checkpoint_path=checkpoint_path,
        eeg_topic=eeg_topic,
        ehr_topic=ehr_topic,
        starting_offsets="earliest",
    )
    print(f"[speed_layer] streaming query id={query.id}  name={query.name}", flush=True)
    query.awaitTermination()
    return 0


if __name__ == "__main__":
    sys.exit(main())

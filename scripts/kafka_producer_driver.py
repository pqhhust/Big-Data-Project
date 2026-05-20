#!/usr/bin/env python3
"""Continuously publish bronze EEG/EHR events from the lake into Kafka.

Reads the JSONL files that ``BronzeWriter`` produced under
``data/lake/bronze/{eeg,ehr}/`` and republishes each line as a Kafka
message on ``eeg.raw`` / ``ehr.updates``.

Designed to run as a long-lived Kubernetes Deployment so the live Spark
streaming pipeline always has events to consume. When the producer hits
the end of the bronze files it loops back to the start with a fresh
``event_time`` (so the watermark stays current).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from kafka import KafkaProducer
from kafka.errors import KafkaError


def _make_producer(bootstrap: str) -> KafkaProducer:
    return KafkaProducer(
        bootstrap_servers=bootstrap,
        acks="all",
        retries=5,
        max_in_flight_requests_per_connection=1,
        key_serializer=lambda k: k.encode("utf-8") if k else None,
        value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
        linger_ms=20,
        compression_type="gzip",
    )


def _iter_jsonl(paths: list[str]):
    for p in paths:
        with open(p) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap", default=os.environ.get("KAFKA_BOOTSTRAP", "kafka-0.kafka.brainwatch.svc.cluster.local:9092"))
    parser.add_argument("--eeg-glob", default="/data/lake/bronze/eeg/**/*.jsonl")
    parser.add_argument("--ehr-glob", default="/data/lake/bronze/ehr/**/*.jsonl")
    parser.add_argument("--eeg-topic", default="eeg.raw")
    parser.add_argument("--ehr-topic", default="ehr.updates")
    parser.add_argument("--rate", type=int, default=200,
                        help="Messages per second TOTAL across both topics")
    parser.add_argument("--retime", action="store_true", default=True,
                        help="Rewrite event_time to 'now - small jitter' so the watermark stays live")
    args = parser.parse_args()

    eeg_files = sorted(glob.glob(args.eeg_glob, recursive=True))
    ehr_files = sorted(glob.glob(args.ehr_glob, recursive=True))
    if not eeg_files:
        print(f"[producer] no EEG bronze files matched {args.eeg_glob}", file=sys.stderr)
        return 1
    print(f"[producer] eeg files: {len(eeg_files)}  ehr files: {len(ehr_files)}")
    print(f"[producer] bootstrap={args.bootstrap}  rate={args.rate}/s  topics=({args.eeg_topic},{args.ehr_topic})")

    producer = _make_producer(args.bootstrap)

    # Pacing: schedule one EEG and one EHR alternately, sleep between bursts
    eeg_iter = _iter_jsonl(eeg_files)
    ehr_iter = _iter_jsonl(ehr_files)
    total = 0
    eeg_sent = 0
    ehr_sent = 0
    last_log = time.time()
    sleep_per_msg = 1.0 / max(args.rate, 1)

    def _rewrite_time(payload: dict) -> dict:
        if args.retime:
            payload = dict(payload)
            payload["event_time"] = datetime.now(timezone.utc).isoformat()
        return payload

    while True:
        try:
            eeg_event = next(eeg_iter)
        except StopIteration:
            eeg_iter = _iter_jsonl(eeg_files)
            eeg_event = next(eeg_iter)
        eeg_event = _rewrite_time(eeg_event)
        producer.send(args.eeg_topic, key=eeg_event.get("patient_id"), value=eeg_event)
        eeg_sent += 1
        total += 1

        try:
            ehr_event = next(ehr_iter)
        except StopIteration:
            ehr_iter = _iter_jsonl(ehr_files) if ehr_files else iter([])
            try:
                ehr_event = next(ehr_iter)
            except StopIteration:
                ehr_event = None
        if ehr_event is not None:
            ehr_event = _rewrite_time(ehr_event)
            producer.send(args.ehr_topic, key=ehr_event.get("patient_id"), value=ehr_event)
            ehr_sent += 1
            total += 1

        # Light pacing so we don't blow Kafka up
        time.sleep(sleep_per_msg * 2)

        now = time.time()
        if now - last_log >= 5.0:
            try:
                producer.flush(timeout=2.0)
            except KafkaError as e:
                print(f"[producer] flush error: {e}", file=sys.stderr)
            print(f"[producer] sent eeg={eeg_sent} ehr={ehr_sent} total={total} ({total/max(now-last_log,1e-6):.0f}/s)",
                  flush=True)
            last_log = now
            eeg_sent = ehr_sent = 0


if __name__ == "__main__":
    raise SystemExit(main())

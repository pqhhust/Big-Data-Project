#!/usr/bin/env python3
"""End-to-end demo: replay → bronze → silver → gold → alerts.

Owner: **Trang**.
Two run modes:

    # Local Docker Compose stack (Kafka + Spark + Cassandra in containers):
    python scripts/end_to_end_demo.py --mode local

    # On the lab Kubernetes cluster:
    python scripts/end_to_end_demo.py --mode k8s

The demo run is the single artefact that proves the whole project works
together. Every other test mocks one layer; this one runs all of them.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path


DEFAULT_MANIFEST = Path("artifacts/demo/mini_manifest.json")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--mode", choices=["local", "k8s"], default="local")
    p.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    p.add_argument("--kafka", default="localhost:9094",
                   help="Kafka bootstrap (override per mode)")
    p.add_argument("--cassandra", default="localhost",
                   help="Cassandra contact point")
    p.add_argument("--bronze", type=Path, default=Path("data/lake/bronze"))
    p.add_argument("--min-critical-alerts", type=int, default=1,
                   help="Demo fails if fewer critical alerts arrive")
    p.add_argument("--timeout", type=int, default=300,
                   help="Seconds to wait for the pipeline to settle")
    return p


def step_1_replay(args) -> dict:
    """Step 1: feed the mini manifest into Kafka.

    Trang: implement. Call ``scripts.replay_to_kafka`` programmatically
    or shell out — your choice. Return ``{"eeg_published": N, "ehr_published": M}``.
    """
    # Trang: code the replay step here.
    pass


def step_2_wait_for_bronze(args) -> int:
    """Step 2: poll the bronze directory until at least one parquet file lands.

    Trang: implement.
      - if mode=k8s, ``kubectl exec`` into a debug pod or use port-forward.
      - if mode=local, just glob ``data/lake/bronze/eeg/**/*.parquet``.
      - timeout = args.timeout seconds, sleep 5 between polls.
      - return the count of parquet files found.
    """
    # Trang: code the bronze poll here.
    pass


def step_3_trigger_batch(args) -> None:
    """Step 3: trigger the silver + gold batch.

    Trang: implement.
      - local: run ``python -m brainwatch.processing.silver_layer ...`` then
        ``... gold_layer ...``.
      - k8s: ``kubectl create job ... --from=cronjob/spark-batch`` and tail
        logs until completion.
    """
    # Trang: code the batch trigger here.
    pass


def step_4_query_alerts(args) -> list[dict]:
    """Step 4: query Cassandra for critical alerts in the last 5 minutes.

    Trang: import ``brainwatch.serving.cassandra_sink`` and call
    ``query_recent_alerts`` for each patient in the manifest. Aggregate.
    Return the list of alert rows.
    """
    # Trang: code the alert query here.
    pass


def step_5_summary(args, replay_stats, bronze_files, alerts) -> None:
    """Step 5: print a one-page summary. Pass/fail decision is here.

    Trang: implement.
      - assert ``len([a for a in alerts if a['severity'] == 'critical'])
              >= args.min_critical_alerts``.
      - print a table of severity counts.
      - print the top 5 alerts (patient_id, time, severity, score).
      - exit code 0 on success, 1 on failure.
    """
    # Trang: code the summary + assertion here.
    pass


def main() -> None:
    args = build_parser().parse_args()
    started = time.time()
    print(f"[demo] mode={args.mode}  manifest={args.manifest}")

    replay_stats = step_1_replay(args)
    bronze_files = step_2_wait_for_bronze(args)
    step_3_trigger_batch(args)
    alerts = step_4_query_alerts(args)
    step_5_summary(args, replay_stats, bronze_files, alerts)

    print(f"[demo] total_seconds={time.time() - started:.1f}")


if __name__ == "__main__":
    main()

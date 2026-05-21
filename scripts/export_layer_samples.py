#!/usr/bin/env python3
"""Export sample rows + row counts from each data-lake layer for the web explorer.

Lets you *see* the data at every Lambda stage in Grafana:
    bronze (raw measured EEG + EHR JSONL)
      → silver (deduped EEG with quality_flag, latest-version EHR)
        → gold (per-patient daily features)

Writes to ``--out`` (default dashboard/public/explorer/):
    lake_stats.json         — [{zone, dataset, rows, columns}]
    bronze_eeg_sample.json  · bronze_ehr_sample.json
    silver_eeg_sample.json  · silver_ehr_sample.json
    gold_sample.json
"""
from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path


def _read_jsonl_sample(glob_pat: str, n: int) -> list[dict]:
    rows: list[dict] = []
    for fp in sorted(glob.glob(glob_pat, recursive=True)):
        with open(fp) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
                if len(rows) >= n:
                    return rows
    return rows


def _count_jsonl(glob_pat: str) -> int:
    total = 0
    for fp in glob.glob(glob_pat, recursive=True):
        with open(fp) as f:
            total += sum(1 for ln in f if ln.strip())
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bronze", type=Path, default=Path("data/lake/bronze_real"))
    parser.add_argument("--silver", type=Path, default=Path("data/lake/silver_real"))
    parser.add_argument("--gold",   type=Path, default=Path("data/lake/gold_real"))
    parser.add_argument("--out",    type=Path, default=Path("dashboard/public/explorer"))
    parser.add_argument("--sample", type=int, default=60)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    def dump(name, data):
        (args.out / name).write_text(json.dumps(data, default=str))

    stats = []

    # ── Bronze (JSONL, read directly) ─────────────────────────────────
    bronze_eeg = _read_jsonl_sample(str(args.bronze / "eeg/**/*.jsonl"), args.sample)
    bronze_ehr = _read_jsonl_sample(str(args.bronze / "ehr/**/*.jsonl"), args.sample)
    dump("bronze_eeg_sample.json", bronze_eeg)
    dump("bronze_ehr_sample.json", bronze_ehr)
    stats.append({"zone": "Bronze", "dataset": "eeg (measured EDF events)",
                  "rows": _count_jsonl(str(args.bronze / "eeg/**/*.jsonl")),
                  "columns": len(bronze_eeg[0]) if bronze_eeg else 0, "format": "JSONL"})
    stats.append({"zone": "Bronze", "dataset": "ehr (real HEEDB ICD-10)",
                  "rows": _count_jsonl(str(args.bronze / "ehr/**/*.jsonl")),
                  "columns": len(bronze_ehr[0]) if bronze_ehr else 0, "format": "JSONL"})

    # ── Silver / Gold (Parquet via Spark) ─────────────────────────────
    from pyspark.sql import SparkSession
    spark = (SparkSession.builder.appName("brainwatch-explorer")
             .master("local[4]").config("spark.driver.memory", "4g")
             .config("spark.ui.enabled", "false").getOrCreate())
    spark.sparkContext.setLogLevel("ERROR")
    import atexit
    atexit.register(spark.stop)

    def parquet_sample_and_count(path, name, label):
        df = spark.read.parquet(str(path))
        sample = [r.asDict(recursive=True) for r in df.limit(args.sample).collect()]
        dump(name, sample)
        stats.append({"zone": label.split("/")[0], "dataset": label,
                      "rows": df.count(), "columns": len(df.columns), "format": "Parquet+Snappy"})

    parquet_sample_and_count(args.silver / "eeg", "silver_eeg_sample.json", "Silver/eeg (dedup + quality_flag)")
    parquet_sample_and_count(args.silver / "ehr", "silver_ehr_sample.json", "Silver/ehr (latest version)")
    parquet_sample_and_count(args.gold / "patient_features", "gold_sample.json", "Gold/patient_features")

    dump("lake_stats.json", stats)
    print(json.dumps({"lake_stats": stats, "out": str(args.out)}, indent=2, default=str))
    spark.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

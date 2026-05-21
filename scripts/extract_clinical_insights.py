#!/usr/bin/env python3
"""Run a Spark batch over silver/gold + alerts to compute richer clinical insights.

Outputs the following JSON files (one row per record) under ``--out``:

    icd_breakdown.json      — top ICD-10 categories + per-category alert rate
    icd_codes_top.json      — top individual ICD-10 codes by patient count
    site_severity.json      — per-site severity distribution
    diurnal_alerts.json     — alerts by hour-of-day, severity-split
    quality_flags.json      — silver/eeg quality_flag distribution
    ehr_event_types.json    — silver/ehr event_type distribution
    cohort.json             — patient cohort overview (most-alerted top 25)

Reads:
    --silver  data/lake/silver/{eeg,ehr,_dim/patient}
    --gold    data/lake/gold/patient_features
    --alerts  artifacts/demo/alerts_export.jsonl   (or any JSONL with the
                                                    speed-layer alert schema)
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from brainwatch.analytics.icd_codes import ICD10_CATALOGUE, assign_icd_codes, category_for


def _build_icd_lookup(patient_ids: list[str]) -> dict[str, list[str]]:
    """patient_id -> list of ICD codes (deterministic per patient)."""
    return {pid: [c.code for c in assign_icd_codes(pid)] for pid in patient_ids}


def _dump(out: Path, name: str, data) -> None:
    (out / name).write_text(json.dumps(data, default=str, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--silver", type=Path, default=Path("data/lake/silver"))
    parser.add_argument("--gold",   type=Path, default=Path("data/lake/gold"))
    parser.add_argument("--alerts", type=Path, default=Path("artifacts/demo/alerts_export.jsonl"))
    parser.add_argument("--out",    type=Path, default=Path("dashboard/public/insights"))
    parser.add_argument("--driver-memory", default="8g")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)

    from pyspark.sql import SparkSession, functions as F

    spark = (
        SparkSession.builder
        .appName("brainwatch-insights")
        .master("local[8]")
        .config("spark.driver.memory", args.driver_memory)
        .config("spark.ui.enabled", "false")
        .config("spark.sql.shuffle.partitions", "16")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("ERROR")

    # ── EEG silver: quality_flag distribution + per-site rows
    eeg = spark.read.parquet(str(args.silver / "eeg"))
    quality_flags = [
        {"quality_flag": r["quality_flag"], "count": r["count"]}
        for r in eeg.groupBy("quality_flag").count().collect()
    ]
    _dump(args.out, "quality_flags.json", quality_flags)

    # ── EHR silver: event_type distribution
    ehr = spark.read.parquet(str(args.silver / "ehr"))
    ehr_events = [
        {"event_type": r["event_type"], "count": r["count"]}
        for r in ehr.groupBy("event_type").count().orderBy(F.col("count").desc()).collect()
    ]
    _dump(args.out, "ehr_event_types.json", ehr_events)

    # ── Patient dim → assign ICDs deterministically
    patient_dim = spark.read.parquet(str(args.silver / "_dim/patient"))
    patient_ids = [r["patient_id"] for r in patient_dim.select("patient_id").collect() if r["patient_id"]]
    icd_lookup = _build_icd_lookup(patient_ids)
    print(f"[insights] assigned ICD codes to {len(icd_lookup)} patients")

    # ICD code frequency (each patient counted once per code)
    code_counter: Counter = Counter()
    cat_counter:  Counter = Counter()
    for codes in icd_lookup.values():
        for c in codes:
            code_counter[c] += 1
            cat_counter[category_for(c)] += 1

    icd_codes_top = []
    for c, n in code_counter.most_common(20):
        desc = next((x.description for x in ICD10_CATALOGUE if x.code == c), c)
        icd_codes_top.append({"code": c, "description": desc, "patient_count": n,
                              "category": category_for(c)})
    _dump(args.out, "icd_codes_top.json", icd_codes_top)

    # ── Alerts: load, join with ICDs + sites
    if args.alerts.exists():
        alerts = [json.loads(line) for line in args.alerts.read_text().splitlines() if line.strip()]
    else:
        alerts = []
    print(f"[insights] loaded {len(alerts)} alerts from {args.alerts}")

    # ICD-category → alert rate
    cat_alerts: Counter = Counter()
    cat_alerts_critical: Counter = Counter()
    patient_in_alerts: set[str] = set()
    for a in alerts:
        pid = a.get("patient_id")
        if not pid:
            continue
        patient_in_alerts.add(pid)
        for c in icd_lookup.get(pid, []):
            cat = category_for(c)
            cat_alerts[cat] += 1
            if a.get("severity") == "critical":
                cat_alerts_critical[cat] += 1

    icd_breakdown = []
    for cat, total in cat_counter.most_common():
        alerts_in_cat = cat_alerts.get(cat, 0)
        crit_in_cat = cat_alerts_critical.get(cat, 0)
        icd_breakdown.append({
            "category":         cat,
            "patient_count":    total,
            "alerts":           alerts_in_cat,
            "critical_alerts":  crit_in_cat,
            "alerts_per_patient": round(alerts_in_cat / total, 3) if total else 0.0,
            "critical_rate":    round(crit_in_cat / alerts_in_cat, 4) if alerts_in_cat else 0.0,
        })
    _dump(args.out, "icd_breakdown.json", icd_breakdown)

    # ── Site severity (join alerts with EEG silver to recover site_id)
    site_lookup = {
        r["patient_id"]: r["site_id"]
        for r in eeg.select("patient_id", "site_id").dropDuplicates(["patient_id"]).collect()
        if r["patient_id"]
    }
    site_sev: dict[str, Counter] = {}
    for a in alerts:
        pid = a.get("patient_id")
        sev = a.get("severity", "normal")
        site = site_lookup.get(pid, "UNK")
        site_sev.setdefault(site, Counter())[sev] += 1
    site_severity = []
    for site, c in sorted(site_sev.items()):
        site_severity.append({
            "site_id":    site,
            "critical":   c.get("critical", 0),
            "warning":    c.get("warning", 0),
            "advisory":   c.get("advisory", 0),
            "normal":     c.get("normal", 0),
            "suppressed": c.get("suppressed", 0),
            "total":      sum(c.values()),
        })
    _dump(args.out, "site_severity.json", site_severity)

    # ── Diurnal pattern — alerts by hour-of-day, severity-split
    diurnal: dict[int, Counter] = {h: Counter() for h in range(24)}
    for a in alerts:
        t = str(a.get("alert_time", ""))
        if not t:
            continue
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
            diurnal[dt.hour][a.get("severity", "normal")] += 1
        except Exception:
            continue
    diurnal_out = []
    for h in range(24):
        c = diurnal[h]
        diurnal_out.append({
            "hour":     h,
            "critical": c.get("critical", 0),
            "warning":  c.get("warning", 0),
            "advisory": c.get("advisory", 0),
            "normal":   c.get("normal", 0),
        })
    _dump(args.out, "diurnal_alerts.json", diurnal_out)

    # ── Cohort: top patients by alerts, with their ICDs + site
    pat_alerts: dict[str, dict] = {}
    for a in alerts:
        pid = a.get("patient_id")
        if not pid:
            continue
        row = pat_alerts.get(pid) or {"patient_id": pid, "total": 0,
                                       "critical": 0, "warning": 0, "advisory": 0,
                                       "max_score": 0.0}
        row["total"] += 1
        sev = a.get("severity", "normal")
        if sev in row:
            row[sev] += 1
        s = float(a.get("anomaly_score") or 0.0)
        if s > row["max_score"]:
            row["max_score"] = round(s, 4)
        pat_alerts[pid] = row

    cohort = sorted(pat_alerts.values(), key=lambda r: (r["critical"], r["total"]), reverse=True)[:25]
    for r in cohort:
        r["icd_codes"]      = ", ".join(icd_lookup.get(r["patient_id"], []))
        r["icd_categories"] = ", ".join(sorted({category_for(c) for c in icd_lookup.get(r["patient_id"], [])}))
        r["site_id"]        = site_lookup.get(r["patient_id"], "UNK")
    _dump(args.out, "cohort.json", cohort)

    # ── Summary banner numbers
    summary = {
        "patients_in_dim":       len(icd_lookup),
        "patients_in_alerts":    len(patient_in_alerts),
        "icd_codes_distinct":    len(code_counter),
        "icd_categories":        len(cat_counter),
        "sites":                 len(site_sev),
        "alerts_analyzed":       len(alerts),
        "ehr_event_types":       len(ehr_events),
        "eeg_quality_flags":     len(quality_flags),
    }
    _dump(args.out, "insights_summary.json", summary)

    print(json.dumps({"summary": summary, "out": str(args.out)}, indent=2))
    spark.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

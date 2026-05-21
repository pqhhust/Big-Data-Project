#!/usr/bin/env python3
"""Generate EHR bronze events for the real EEG cohort using real HEEDB ICD-10.

For every distinct patient in ``bronze_real/eeg`` this looks up the patient's
*real* neurology diagnosis categories from the HEEDB ICD-10 table and emits
EHR events whose payload carries those real categories. High-acuity diagnoses
(stroke, seizure, tumor, infection, MND) raise the critical-lab rate so the
downstream anomaly scoring reflects real clinical risk.
"""
from __future__ import annotations

import argparse
import glob
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from brainwatch.analytics.heedb import load_heedb_icd, categories_for, HIGH_ACUITY

_TYPES = ["vital_signs", "lab_result", "medication", "critical_lab", "note"]


def _distinct_patients(bronze_eeg_glob: str) -> dict[str, str]:
    """patient_id -> site_id from the real EEG bronze."""
    out: dict[str, str] = {}
    for fp in glob.glob(bronze_eeg_glob, recursive=True):
        with open(fp) as f:
            line = f.readline().strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("patient_id"):
            out[e["patient_id"]] = e.get("site_id", "UNK")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bronze", type=Path, default=Path("data/lake/bronze_real"))
    parser.add_argument("--heedb", default="data/raw/metadata/HEEDB_ICD10_for_Neurology.csv")
    parser.add_argument("--events-per-patient", type=int, default=12)
    parser.add_argument("--seed", type=int, default=11)
    args = parser.parse_args()

    heedb = load_heedb_icd(args.heedb)
    print(f"[real-ehr] HEEDB patients loaded: {len(heedb):,}")

    patients = _distinct_patients(str(args.bronze / "eeg/**/*.jsonl"))
    print(f"[real-ehr] real EEG patients: {len(patients)}")

    rng = random.Random(args.seed)
    base = datetime(2026, 5, 19, 8, 0, 0, tzinfo=timezone.utc)
    out_dir = args.bronze / "ehr" / "date=2026-05-19"
    out_dir.mkdir(parents=True, exist_ok=True)

    n_events = 0
    n_matched = 0
    with (out_dir / "ehr_real.jsonl").open("w") as f:
        for pid, site in patients.items():
            cats = categories_for(pid, heedb)
            if cats:
                n_matched += 1
            high_acuity = bool(set(cats) & HIGH_ACUITY)
            crit_p = 0.22 if high_acuity else 0.05
            weights = [0.30, 0.25, 0.20, crit_p, 0.15]
            for _ in range(args.events_per_patient):
                et = rng.choices(_TYPES, weights=weights, k=1)[0]
                evt = {
                    "patient_id": pid,
                    "encounter_id": f"enc-{pid}",
                    "event_time": (base + timedelta(minutes=rng.randint(-30, 120))).isoformat(),
                    "event_type": et,
                    "source_system": rng.choice(["epic", "cerner"]),
                    "version": rng.randint(1, 3),
                    "payload": {
                        "icd_categories": cats,
                        "icd_primary": cats[0] if cats else None,
                        "high_acuity": high_acuity,
                        "site_id": site,
                        "code": rng.choice(["HR", "BP", "O2", "TEMP", "GLU", "K", "NA", "CR"]),
                        "value": round(rng.uniform(50, 200), 2),
                        "unit": rng.choice(["mmHg", "bpm", "mg/dL", "C"]),
                    },
                }
                f.write(json.dumps(evt) + "\n")
                n_events += 1

    print(json.dumps({
        "ehr_events": n_events,
        "patients": len(patients),
        "patients_with_real_icd": n_matched,
        "match_rate": round(n_matched / len(patients), 3) if patients else 0,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

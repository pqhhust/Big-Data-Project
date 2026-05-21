"""Loader for the real BDSP HEEDB ICD-10 neurology table.

``HEEDB_ICD10_for_Neurology.csv`` holds, per ``BDSPPatientID``, a binary flag
for each of 18 neurology diagnosis categories plus demographics. This module
turns it into a ``patient_numeric_id -> [positive categories]`` lookup so the
insights pipeline can report *real* diagnoses for the downloaded EEG cohort.
"""
from __future__ import annotations

import csv
import re
from functools import lru_cache
from pathlib import Path

_NON_CATEGORY = {"BDSPPatientID", "SiteID", "SexDSC", "VisitCount", "AgeAtVisitAvg"}
_PID_RE = re.compile(r"^[A-Z]\d{4}(\d+)$")


def patient_numeric_id(patient_id: str) -> str | None:
    """Strip the BIDS site prefix: 'S0001119303867' -> '119303867'."""
    if not patient_id:
        return None
    m = _PID_RE.match(patient_id)
    if m:
        return m.group(1)
    # already numeric?
    return patient_id if patient_id.isdigit() else None


@lru_cache(maxsize=4)
def load_heedb_icd(csv_path: str) -> dict:
    """Return {bdsp_patient_id: {"categories": [...], "sex": str, "age": float|None}}."""
    out: dict[str, dict] = {}
    p = Path(csv_path)
    if not p.exists():
        return out
    with p.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        cats = [c for c in reader.fieldnames or [] if c not in _NON_CATEGORY]
        for row in reader:
            pid = (row.get("BDSPPatientID") or "").strip()
            if not pid:
                continue
            positive = [c for c in cats if (row.get(c) or "").strip() not in ("", "0", "0.0")]
            age_raw = (row.get("AgeAtVisitAvg") or "").strip()
            try:
                age = float(age_raw) if age_raw else None
            except ValueError:
                age = None
            out[pid] = {
                "categories": positive,
                "sex": (row.get("SexDSC") or "").strip() or "U",
                "age": age,
            }
    return out


def categories_for(patient_id: str, heedb: dict) -> list[str]:
    num = patient_numeric_id(patient_id)
    if num is None:
        return []
    rec = heedb.get(num)
    return list(rec["categories"]) if rec else []


# Categories considered high-acuity — drive a higher synthetic critical-lab rate
HIGH_ACUITY = frozenset({
    "Cerebrovascular Diseases",
    "Seizure Disorders",
    "Intracranial And Spinal Tumors",
    "Infections",
    "Motor Neuron Diseases",
})

"""Neurology-focused ICD-10 code catalogue + deterministic patient assignment.

Real EHR feeds include ICD-10 problem-list / discharge-diagnosis codes; our
synthetic generator did not. This module makes the synthetic dataset
clinically plausible without re-running ingestion: codes are computed
deterministically from ``patient_id`` so the same patient always carries the
same diagnoses across pipeline runs.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class IcdCode:
    code: str
    description: str
    category: str
    weight: float


# Weighted neurology + adjacent codes commonly seen in a neuro-ICU.
# Weights are illustrative ICU prevalences (not from a real registry).
ICD10_CATALOGUE: tuple[IcdCode, ...] = (
    IcdCode("G40.901", "Epilepsy, unspecified, with status epilepticus", "Seizure",       0.18),
    IcdCode("G40.909", "Epilepsy, unspecified, w/o status epilepticus",  "Seizure",       0.15),
    IcdCode("G93.40",  "Encephalopathy, unspecified",                    "Encephalopathy",0.14),
    IcdCode("R56.9",   "Unspecified convulsions",                        "Seizure",       0.11),
    IcdCode("I63.9",   "Cerebral infarction, unspecified",               "Stroke",        0.09),
    IcdCode("I61.9",   "Nontraumatic intracerebral hemorrhage",          "Stroke",        0.06),
    IcdCode("S06.9X0A","Unspecified intracranial injury",                "TBI",           0.05),
    IcdCode("G35",     "Multiple sclerosis",                             "Demyelinating", 0.03),
    IcdCode("F03.90",  "Unspecified dementia",                           "Cognitive",     0.04),
    IcdCode("R40.20",  "Unspecified coma",                               "Altered LOC",   0.04),
    IcdCode("G45.9",   "Transient cerebral ischemic attack",             "TIA",           0.03),
    IcdCode("G93.41",  "Metabolic encephalopathy",                       "Encephalopathy",0.03),
    IcdCode("G44.1",   "Vascular headache, NEC",                         "Headache",      0.02),
    IcdCode("G70.00",  "Myasthenia gravis w/o exacerbation",             "Neuromuscular", 0.02),
    IcdCode("I60.9",   "Subarachnoid hemorrhage, unspecified",           "Stroke",        0.01),
)


def _hash_to_unit(s: str, salt: str) -> float:
    h = hashlib.sha1(f"{salt}|{s}".encode("utf-8")).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def assign_icd_codes(patient_id: str, n_min: int = 1, n_max: int = 3) -> list[IcdCode]:
    """Deterministically pick 1-3 ICD-10 codes for a patient.

    The same ``patient_id`` always yields the same code list across runs,
    which lets the dashboard show stable per-patient diagnoses.
    """
    if not patient_id:
        return []
    # Choose count
    u_count = _hash_to_unit(patient_id, "count")
    count = int(n_min + u_count * (n_max - n_min + 1))
    count = max(n_min, min(n_max, count))

    # Weighted sampling without replacement
    catalogue = list(ICD10_CATALOGUE)
    chosen: list[IcdCode] = []
    for i in range(count):
        u = _hash_to_unit(patient_id, f"pick-{i}")
        # Cumulative weighted pick
        weights = [c.weight for c in catalogue]
        total = sum(weights)
        target = u * total
        accum = 0.0
        idx = 0
        for j, w in enumerate(weights):
            accum += w
            if accum >= target:
                idx = j
                break
        chosen.append(catalogue[idx])
        catalogue.pop(idx)
    return chosen


def category_for(code: str) -> str:
    for c in ICD10_CATALOGUE:
        if c.code == code:
            return c.category
    return "Other"

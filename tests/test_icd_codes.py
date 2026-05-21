"""Tests for ``brainwatch.analytics.icd_codes``."""
from __future__ import annotations

from brainwatch.analytics.icd_codes import (
    ICD10_CATALOGUE, assign_icd_codes, category_for,
)


def test_catalogue_is_non_empty_and_weighted():
    assert len(ICD10_CATALOGUE) >= 10
    assert all(c.code and c.description and c.category for c in ICD10_CATALOGUE)
    assert sum(c.weight for c in ICD10_CATALOGUE) > 0


def test_deterministic_assignment_for_same_patient():
    a = assign_icd_codes("subject-123-c001")
    b = assign_icd_codes("subject-123-c001")
    assert a == b


def test_different_patients_get_different_codes():
    a = assign_icd_codes("subject-A-c001")
    b = assign_icd_codes("subject-B-c001")
    # At least one of the picks differs (collision is technically possible
    # but vanishingly unlikely with our 15-item catalogue and SHA-1 hashing).
    assert {x.code for x in a} != {x.code for x in b} or len(a) != len(b)


def test_assignment_respects_count_bounds():
    codes = assign_icd_codes("anything", n_min=2, n_max=2)
    assert len(codes) == 2


def test_category_for_known_and_unknown():
    assert category_for("G40.901") == "Seizure"
    assert category_for("ZZZ.99")  == "Other"

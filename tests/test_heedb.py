"""Tests for the real HEEDB ICD-10 loader."""
from __future__ import annotations

from pathlib import Path

import pytest

from brainwatch.analytics.heedb import (
    HIGH_ACUITY, categories_for, load_heedb_icd, patient_numeric_id,
)


def test_patient_numeric_id_strips_site_prefix():
    assert patient_numeric_id("S0001119303867") == "119303867"
    assert patient_numeric_id("I0002150018020") == "150018020"


def test_patient_numeric_id_passes_through_plain_numeric():
    assert patient_numeric_id("119303867") == "119303867"


def test_patient_numeric_id_handles_empty_and_garbage():
    assert patient_numeric_id("") is None
    assert patient_numeric_id("not-an-id") is None


def test_load_missing_file_returns_empty():
    assert load_heedb_icd("/no/such/heedb.csv") == {}


def test_load_and_lookup_real_table_if_present():
    csv_path = "data/raw/metadata/HEEDB_ICD10_for_Neurology.csv"
    if not Path(csv_path).exists():
        pytest.skip("HEEDB CSV not downloaded in this environment")
    heedb = load_heedb_icd(csv_path)
    assert len(heedb) > 1000
    # every record exposes categories/sex/age
    sample = next(iter(heedb.values()))
    assert set(sample) == {"categories", "sex", "age"}
    assert isinstance(sample["categories"], list)


def test_categories_for_unknown_patient_is_empty():
    assert categories_for("S0001999999999", {}) == []


def test_high_acuity_set_is_frozen_and_nonempty():
    assert isinstance(HIGH_ACUITY, frozenset)
    assert "Seizure Disorders" in HIGH_ACUITY

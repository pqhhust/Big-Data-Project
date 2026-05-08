"""Tests for ``scripts/download_eeg_ehr.py``.

Owner: **Trang** (you wrote the script — you write the tests).

We test the **manifest-building** logic only — the boto3 download path is
tested manually with a real bucket, since mocking S3 is more trouble than it's
worth at this stage.
"""
from __future__ import annotations

import csv
from pathlib import Path

# Trang: once your script lands, uncomment:
#   from scripts.download_eeg_ehr import build_manifest, load_aws_credentials


def _write_csv(path: Path, rows: list[dict]) -> None:
    """Trang: write a tiny BDSP-shaped metadata CSV.  Required columns at minimum:
    subject_id, session_id, site_id, duration_seconds, s3_key (or s3_keys list)."""
    pass


def test_build_manifest_filters_by_duration(tmp_path: Path):
    """Trang: write a CSV with one row at 30s (too short), one at 600s (good),
    one at 9000s (too long). build_manifest with min=60, max=1200 keeps only
    the 600s row."""
    pass


def test_build_manifest_stops_at_target_hours(tmp_path: Path):
    """Trang: write a CSV with 100 rows of 60s each. target_hours=1 should stop
    after exactly 60 records (1h = 3600s = 60 * 60s)."""
    pass


def test_load_aws_credentials_parses_csv(tmp_path: Path):
    """Trang: write a 2-line CSV with header ``Access key ID,Secret access key``
    and a fake row. Assert the loader returns the dict shape boto3 expects:
    ``{"aws_access_key_id": ..., "aws_secret_access_key": ...}``.

    DO NOT use the real credentials file in this test — write a fake one in
    tmp_path."""
    pass

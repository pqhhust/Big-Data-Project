"""Tests for ``scripts/download_eeg_ehr.py``.

Owner: **Trang** (you wrote the script — you write the tests).

We test the **manifest-building** logic only — the boto3 download path is
tested manually with a real bucket, since mocking S3 is more trouble than it's
worth at this stage.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from scripts.download_eeg_ehr import build_manifest, load_aws_credentials


def _write_csv(path: Path, rows: list[dict]) -> None:
    """Write a tiny BDSP-shaped metadata CSV."""
    with path.open("w", newline="") as f:
        fieldnames = list(rows[0].keys()) if rows else ["subject_id", "session_id", "site_id", "duration_seconds", "s3_key"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_build_manifest_filters_by_duration(tmp_path: Path):
    """Test duration filtering: keep only rows between min and max."""
    csv_dir = tmp_path / "metadata"
    csv_dir.mkdir()

    # Write CSV with varied durations
    _write_csv(csv_dir / "site_meta.csv", [
        {"subject_id": "P001", "session_id": "S001", "site_id": "SITE01", "duration_seconds": 30, "s3_key": "s3://bucket/file1.edf"},  # too short
        {"subject_id": "P002", "session_id": "S002", "site_id": "SITE01", "duration_seconds": 600, "s3_key": "s3://bucket/file2.edf"},  # good
        {"subject_id": "P003", "session_id": "S003", "site_id": "SITE01", "duration_seconds": 9000, "s3_key": "s3://bucket/file3.edf"},  # too long
    ])

    manifest = build_manifest(csv_dir, target_hours=10, min_duration=60, max_duration=1200)

    # Only P002 should be included
    assert manifest["subject_count"] == 1
    assert manifest["records"][0]["subject_id"] == "P002"


def test_build_manifest_stops_at_target_hours(tmp_path: Path):
    """Test that manifest stops selecting when target hours is reached."""
    csv_dir = tmp_path / "metadata"
    csv_dir.mkdir()

    # 100 rows of 60s each
    rows = [
        {"subject_id": f"P{i:03d}", "session_id": f"S{i:03d}", "site_id": "SITE01",
         "duration_seconds": 60, "s3_key": f"s3://bucket/file{i}.edf"}
        for i in range(100)
    ]
    _write_csv(csv_dir / "site_meta.csv", rows)

    # 1 hour target = 3600 seconds = 60 records of 60s each
    manifest = build_manifest(csv_dir, target_hours=1, min_duration=1, max_duration=1200)

    assert manifest["subject_count"] == 60
    assert abs(manifest["actual_hours"] - 1.0) < 0.01


def test_load_aws_credentials_parses_csv(tmp_path: Path):
    """Test that credential loader correctly parses the rootkey CSV."""
    creds_file = tmp_path / "rootkey.csv"
    with creds_file.open("w", newline="") as f:
        f.write("Access key ID,Secret access key\n")
        f.write("AKIAIOSFODNN7EXAMPLE,wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY\n")

    creds = load_aws_credentials(creds_file)

    assert creds["aws_access_key_id"] == "AKIAIOSFODNN7EXAMPLE"
    assert creds["aws_secret_access_key"] == "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"


def test_build_manifest_aggregates_sites(tmp_path: Path):
    """Test that manifest correctly counts unique sites."""
    csv_dir = tmp_path / "metadata"
    csv_dir.mkdir()

    # Sites SITE01, SITE02, SITE03 with short durations so all get selected
    # (target_hours=1 = 3600s, each record is 300s, so we'll get 12 records)
    for site_num in range(1, 4):
        rows = [
            {"subject_id": f"P{site_num:02d}{i:02d}", "session_id": f"S{i:02d}",
             "site_id": f"SITE{site_num:02d}", "duration_seconds": 300,
             "s3_key": f"s3://bucket/site{site_num}_subj{i}.edf"}
            for i in range(1, 5)  # 4 records per site
        ]
        _write_csv(csv_dir / f"site{site_num}_meta.csv", rows)

    manifest = build_manifest(csv_dir, target_hours=1, min_duration=1, max_duration=12000)

    # Should get records from all 3 sites since they're all same duration
    assert manifest["site_count"] == 3
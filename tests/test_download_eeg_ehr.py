"""Tests for ``scripts/download_eeg_ehr.py``.

Owner: **Trang** (you wrote the script — you write the tests).

We test the **manifest-building** logic only — the boto3 download path is
tested manually with a real bucket, since mocking S3 is more trouble than it's
worth at this stage.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scripts.download_eeg_ehr import build_manifest, load_aws_credentials


def _write_csv(path: Path, rows: list[dict]) -> None:
    """Write a tiny BDSP-shaped metadata CSV."""
    if not rows:
        raise ValueError("rows must not be empty")

    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_build_manifest_filters_by_duration(tmp_path: Path) -> None:
    csv_path = tmp_path / "sample_meta.csv"
    _write_csv(
        csv_path,
        [
            {
                "subject_id": "sub-1",
                "session_id": "1",
                "site_id": "I0002",
                "duration_seconds": "30",
                "s3_key": "short.edf",
            },
            {
                "subject_id": "sub-2",
                "session_id": "2",
                "site_id": "I0002",
                "duration_seconds": "600",
                "s3_key": "good.edf",
            },
            {
                "subject_id": "sub-3",
                "session_id": "3",
                "site_id": "I0002",
                "duration_seconds": "9000",
                "s3_key": "long.edf",
            },
        ],
    )

    manifest = build_manifest(tmp_path, target_hours=1, min_duration=60, max_duration=1200)

    assert manifest["record_count"] == 1
    assert manifest["records"][0]["subject_id"] == "sub-2"
    assert manifest["records"][0]["s3_keys"] == ["good.edf"]


def test_build_manifest_stops_at_target_hours(tmp_path: Path) -> None:
    csv_path = tmp_path / "bulk_meta.csv"
    rows = [
        {
            "subject_id": f"sub-{index:03d}",
            "session_id": str(index),
            "site_id": "I0002",
            "duration_seconds": "60",
            "s3_key": f"file-{index:03d}.edf",
        }
        for index in range(100)
    ]
    _write_csv(csv_path, rows)

    manifest = build_manifest(tmp_path, target_hours=1, min_duration=60, max_duration=1200)

    assert manifest["record_count"] == 60


def test_load_aws_credentials_parses_csv(tmp_path: Path) -> None:
    creds_path = tmp_path / "rootkey.csv"
    creds_path.write_text(
        "Access key ID,Secret access key\nFAKEKEY,FAKESECRET\n",
        encoding="utf-8",
    )

    creds = load_aws_credentials(creds_path)

    assert creds == {
        "aws_access_key_id": "FAKEKEY",
        "aws_secret_access_key": "FAKESECRET",
    }

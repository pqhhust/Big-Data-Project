"""Tests for scripts/download_eeg_ehr.py — 4 test cases."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.download_eeg_ehr import find_metadata_csvs


class TestFindMetadataCSVs:
    def test_finds_csv_files(self, tmp_path: Path) -> None:
        (tmp_path / "S0001_meta.csv").write_text("header\n", encoding="utf-8")
        (tmp_path / "S0002_meta.csv").write_text("header\n", encoding="utf-8")
        csvs = find_metadata_csvs(tmp_path)
        assert len(csvs) == 2

    def test_empty_directory(self, tmp_path: Path) -> None:
        csvs = find_metadata_csvs(tmp_path)
        assert len(csvs) == 0

    def test_nonexistent_directory(self) -> None:
        csvs = find_metadata_csvs("/nonexistent/path")
        assert csvs == []

    def test_ignores_non_csv_files(self, tmp_path: Path) -> None:
        (tmp_path / "readme.md").write_text("# readme\n", encoding="utf-8")
        (tmp_path / "data.csv").write_text("header\n", encoding="utf-8")
        csvs = find_metadata_csvs(tmp_path)
        assert len(csvs) == 1

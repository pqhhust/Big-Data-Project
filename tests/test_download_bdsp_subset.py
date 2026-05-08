import json
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from download_bdsp_subset import collect_candidates, select_stratified, write_manifest


def _write_csvs(tmp_path: Path) -> Path:
    """Create minimal site CSVs for testing."""
    csv_dir = tmp_path / "meta"
    csv_dir.mkdir()

    # S0001: 3 candidates (short recordings)
    (csv_dir / "S0001_meta.csv").write_text(
        "SiteID,BidsFolder,SessionID,DurationInSeconds,ServiceName,SexDSC\n"
        "S0001,sub-A,1,120.0,Routine,Female\n"
        "S0001,sub-B,2,300.0,LTM,Male\n"
        "S0001,sub-C,3,600.0,LTM,Female\n"
        "S0001,sub-D,4,5000.0,LTM,Male\n"  # > max_duration
    )
    # S0002: 2 candidates
    (csv_dir / "S0002_meta.csv").write_text(
        "SiteID,BidsFolder,SessionID,DurationInSeconds,ServiceName,SexDSC\n"
        "S0002,sub-E,1,180.0,Routine,Female\n"
        "S0002,sub-F,2,900.0,LTM,Male\n"
    )
    # Empty sites
    for site in ("I0002", "I0003", "I0009"):
        (csv_dir / f"{site}_meta.csv").write_text(
            "SiteID,BidsFolder,SessionID,DurationInSeconds\n"
        )
    return csv_dir


def test_collect_candidates_filters_by_duration(tmp_path: Path) -> None:
    csv_dir = _write_csvs(tmp_path)
    candidates = collect_candidates(csv_dir, min_duration=60.0, max_duration=1200.0)
    assert "S0001" in candidates
    assert len(candidates["S0001"]) == 3  # sub-D excluded (5000s)
    assert len(candidates["S0002"]) == 2


def test_select_stratified_round_robin(tmp_path: Path) -> None:
    csv_dir = _write_csvs(tmp_path)
    candidates = collect_candidates(csv_dir, min_duration=60.0, max_duration=1200.0)
    selected = select_stratified(candidates, target_hours=1.0)
    # Should have records from both sites
    sites = {r["site_id"] for r in selected}
    assert "S0001" in sites
    assert "S0002" in sites


def test_write_manifest_creates_file(tmp_path: Path) -> None:
    records = [
        {"site_id": "S0001", "subject_id": "sub-A", "session_id": "1",
         "duration_seconds": 300.0, "s3_keys": ["key1"]},
    ]
    out = tmp_path / "manifest.json"
    summary = write_manifest(records, out)
    assert out.exists()
    assert summary["total_subjects"] == 1
    data = json.loads(out.read_text())
    assert data["manifest_version"] == "2.0"

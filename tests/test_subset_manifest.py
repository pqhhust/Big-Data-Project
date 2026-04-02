from pathlib import Path

from brainwatch.ingestion.subset_manifest import select_subset


def test_select_subset_respects_max_sessions(tmp_path: Path) -> None:
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(
        "\n".join(
            [
                "SiteID,BidsFolder,SessionID,DurationInSeconds,ServiceName",
                "S0001,sub-1,1,3600,LTM",
                "S0001,sub-2,2,3600,Routine",
                "S0002,sub-3,3,3600,LTM",
            ]
        ),
        encoding="utf-8",
    )

    records = select_subset([csv_path], max_sessions=2, target_hours=10)

    assert len(records) == 2
    assert records[0]["subject_id"] == "sub-1"

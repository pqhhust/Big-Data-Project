from pathlib import Path

from brainwatch.ingestion.eeg_inventory import summarize_metadata


def test_summarize_metadata_counts_rows_and_candidate_keys(tmp_path: Path) -> None:
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(
        "\n".join(
            [
                "SiteID,BidsFolder,SessionID,DurationInSeconds,ServiceName,SexDSC",
                "S0001,sub-S0001AAA,1,120.0,LTM,Female",
                "S0002,sub-S0002BBB,2,20.0,LTM,Male",
            ]
        ),
        encoding="utf-8",
    )

    summary = summarize_metadata([csv_path], min_duration=30.0)

    assert summary["total_rows"] == 2
    assert summary["unique_subjects"] == 2
    assert summary["total_candidate_s3_keys"] == 3
    assert summary["short_sessions_below_min_duration"] == 1
    assert summary["rows_by_site"]["S0001"] == 1

from pathlib import Path

from brainwatch.ingestion.dead_letter import DeadLetterQueue


def test_route_writes_to_file(tmp_path: Path) -> None:
    dlq = DeadLetterQueue(tmp_path / "dlq")
    dlq.route({"patient_id": "P1", "bad_field": None}, reason="missing session_id")
    assert dlq.count == 1

    records = dlq.read_all()
    assert len(records) == 1
    assert records[0]["reason"] == "missing session_id"
    assert records[0]["original_payload"]["patient_id"] == "P1"


def test_multiple_routes_same_day(tmp_path: Path) -> None:
    dlq = DeadLetterQueue(tmp_path / "dlq")
    dlq.route({"id": "1"}, reason="err1")
    dlq.route({"id": "2"}, reason="err2")
    assert dlq.count == 2
    records = dlq.read_all()
    assert len(records) == 2


def test_empty_dlq_returns_empty_list(tmp_path: Path) -> None:
    dlq = DeadLetterQueue(tmp_path / "empty_dlq")
    assert dlq.read_all() == []

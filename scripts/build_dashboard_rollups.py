#!/usr/bin/env python3
"""Roll the live alerts JSONL up into per-panel JSON endpoints for Grafana.

Reads an alerts JSONL (default ``dashboard/public/alerts_export.jsonl``) and
emits the per-panel rollups consumed by the dashboards::

    summary.json · severity.json · timeline.json
    score_histogram.json · top_patients.json · recent.json

The aggregation logic lives in :mod:`brainwatch.analytics.rollups` (shared
with the in-cluster Cassandra exporter); this script only adds JSONL reading
and an optional watch loop.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from brainwatch.analytics.rollups import (  # noqa: E402  (re-exported for tests)
    build_recent, build_score_histogram, build_severity_breakdown,
    build_summary, build_timeline, build_top_patients,
)


def _read_alerts(path: Path) -> list[dict]:
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text().splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            rows.append(json.loads(s))
        except json.JSONDecodeError:
            continue
    return rows


def _write_rollups(rows: list[dict], out: Path) -> None:
    artifacts = {
        "summary.json":         build_summary(rows),
        "severity.json":        build_severity_breakdown(rows),
        "timeline.json":        build_timeline(rows),
        "score_histogram.json": build_score_histogram(rows),
        "top_patients.json":    build_top_patients(rows),
        "recent.json":          build_recent(rows),
    }
    for name, data in artifacts.items():
        (out / name).write_text(json.dumps(data, default=str))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alerts", type=Path, default=Path("dashboard/public/alerts_export.jsonl"))
    parser.add_argument("--out", type=Path, default=Path("dashboard/public"))
    parser.add_argument("--watch-interval", type=float, default=0,
                        help="If >0, recompute every N seconds in a loop")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    if args.watch_interval <= 0:
        rows = _read_alerts(args.alerts)
        _write_rollups(rows, args.out)
        print(json.dumps({"rows": len(rows), "out": str(args.out)}))
        return 0

    print(f"[rollups] watching {args.alerts} every {args.watch_interval}s")
    while True:
        t0 = time.time()
        rows = _read_alerts(args.alerts)
        _write_rollups(rows, args.out)
        print(f"[rollups] rows={len(rows)} elapsed={time.time() - t0:.2f}s", flush=True)
        time.sleep(args.watch_interval)


if __name__ == "__main__":
    raise SystemExit(main())

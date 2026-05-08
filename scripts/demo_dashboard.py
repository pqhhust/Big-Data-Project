#!/usr/bin/env python3
"""Demo dashboard for the report screenshots.

Owner: **Trang**.
Two flavours — pick one based on what you can finish in time:

  (a) **matplotlib** (recommended, simpler):
      Reads alerts from Cassandra, generates one PNG per metric, saves them
      under ``artifacts/demo/figures/``. No server needed, deterministic
      output for the report.

  (b) **streamlit** (only if matplotlib is done early):
      ``streamlit run scripts/demo_dashboard.py`` for a live one-page app.

This module is structured so both flavours can coexist — the matplotlib
``render_*`` helpers are independent of the Streamlit UI.

Usage:
    python scripts/demo_dashboard.py --output artifacts/demo/figures/
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any


def fetch_alerts(cassandra_session: Any, since_minutes: int = 60) -> list[dict]:
    """Pull alerts from the last ``since_minutes`` from Cassandra.

    Trang: implement. SELECT from brainwatch.alerts; filter in Python (the
    table is small enough for the demo). If Cassandra is unavailable, fall
    back to reading ``artifacts/demo/alerts_export.jsonl`` so screenshots
    can still be regenerated offline.
    """
    # Trang: code the fetch here.
    pass


def render_severity_histogram(alerts: list[dict], output: Path) -> Path:
    """Bar chart: alert count by severity. Save PNG, return its path.

    Trang: matplotlib, single subplot, count `severity` field.
    """
    # Trang: code the matplotlib render here.
    pass


def render_alert_timeline(alerts: list[dict], output: Path) -> Path:
    """Time-series: alert count per minute over the demo window.

    Trang: bin alerts by minute, plot stacked-by-severity bars.
    """
    # Trang: code the timeline render here.
    pass


def render_signal_quality_distribution(alerts: list[dict],
                                       output: Path) -> Path:
    """Histogram of ``anomaly_score`` for alerted patients.

    Trang: matplotlib hist(), 20 bins, overlay vertical lines at the
    severity thresholds (0.40 / 0.65 / 0.85).
    """
    # Trang: code the histogram here.
    pass


def render_top_patients_table(alerts: list[dict], output: Path) -> Path:
    """A 5-row markdown table of patients with the most critical alerts.

    Trang: write to a ``.md`` file (not PNG) so it can be embedded directly
    in the report.
    """
    # Trang: code the markdown-table render here.
    pass


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--cassandra", default="localhost")
    p.add_argument("--since-minutes", type=int, default=60)
    p.add_argument("--output", type=Path, default=Path("artifacts/demo/figures"))
    return p


def main() -> None:
    args = build_parser().parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    # Trang: implement.
    #   1. session = cassandra_sink.get_session([args.cassandra])
    #   2. alerts = fetch_alerts(session, args.since_minutes)
    #   3. for renderer in (hist, timeline, quality, table):
    #          path = renderer(alerts, args.output / "<name>.{png,md}")
    #          print(f"wrote {path}")
    pass


if __name__ == "__main__":
    main()

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
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_ALERTS_EXPORT = Path("artifacts/demo/alerts_export.jsonl")


def _load_json_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    rows: list[dict[str, Any]] = []
    if path.suffix.lower() == ".jsonl":
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    record = json.loads(line)
                    if isinstance(record, dict):
                        rows.append(record)
        return rows

    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, list):
        return [record for record in payload if isinstance(record, dict)]
    if isinstance(payload, dict):
        records = payload.get("records")
        if isinstance(records, list):
            return [record for record in records if isinstance(record, dict)]
    return []


def _normalise_alert_time(raw_value: Any) -> datetime | None:
    if not raw_value:
        return None

    raw_text = str(raw_value).strip()
    if not raw_text:
        return None

    if raw_text.endswith("Z"):
        raw_text = raw_text[:-1] + "+00:00"

    try:
        return datetime.fromisoformat(raw_text)
    except ValueError:
        return None


def fetch_alerts(cassandra_session: Any, since_minutes: int = 60) -> list[dict]:
    """Pull alerts from the last ``since_minutes`` from Cassandra.

    If Cassandra is unavailable, fall back to reading
    ``artifacts/demo/alerts_export.jsonl`` so screenshots can still be
    regenerated offline.
    """
    _ = since_minutes

    if cassandra_session is not None:
        try:
            rows = cassandra_session.execute("SELECT * FROM brainwatch.alerts")
            return [dict(row) for row in rows]
        except Exception:
            pass

    alerts = _load_json_records(DEFAULT_ALERTS_EXPORT)
    if not alerts:
        return []
    return alerts


def render_severity_histogram(alerts: list[dict], output: Path) -> Path:
    """Bar chart: alert count by severity. Save PNG, return its path.

    Trang: matplotlib, single subplot, count `severity` field.
    """
    from matplotlib import pyplot as plt

    severity_counts = Counter(str(alert.get("severity", "unknown")) for alert in alerts)
    severities = ["critical", "warning", "advisory", "normal", "suppressed", "unknown"]
    values = [severity_counts.get(severity, 0) for severity in severities]

    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=160)
    bars = ax.bar(severities, values, color=["#b91c1c", "#d97706", "#2563eb", "#6b7280", "#4b5563", "#9ca3af"])
    ax.set_title("Alerts by severity")
    ax.set_ylabel("Count")
    ax.set_xlabel("Severity")
    ax.grid(axis="y", alpha=0.2)
    ax.bar_label(bars, padding=3, fontsize=9)
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)
    return output


def render_alert_timeline(alerts: list[dict], output: Path) -> Path:
    """Time-series: alert count per minute over the demo window.

    Trang: bin alerts by minute, plot stacked-by-severity bars.
    """
    from matplotlib import pyplot as plt

    buckets: dict[datetime, Counter[str]] = defaultdict(Counter)
    for alert in alerts:
        alert_time = _normalise_alert_time(alert.get("alert_time"))
        if alert_time is None:
            continue
        minute_bucket = alert_time.replace(second=0, microsecond=0)
        buckets[minute_bucket][str(alert.get("severity", "unknown"))] += 1

    if not buckets:
        buckets[datetime.now().replace(second=0, microsecond=0)]

    ordered_minutes = sorted(buckets.keys())
    severities = ["critical", "warning", "advisory", "normal", "suppressed"]

    fig, ax = plt.subplots(figsize=(10, 4.5), dpi=160)
    bottom = [0] * len(ordered_minutes)
    for severity, color in zip(
        severities,
        ["#b91c1c", "#d97706", "#2563eb", "#6b7280", "#4b5563"],
        strict=False,
    ):
        values = [buckets[minute].get(severity, 0) for minute in ordered_minutes]
        ax.bar(ordered_minutes, values, bottom=bottom, label=severity, color=color, width=0.0008)
        bottom = [current + value for current, value in zip(bottom, values)]

    ax.set_title("Alerts per minute")
    ax.set_ylabel("Count")
    ax.legend(ncol=3, fontsize=8)
    fig.autofmt_xdate()
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)
    return output


def render_signal_quality_distribution(alerts: list[dict],
                                       output: Path) -> Path:
    """Histogram of ``anomaly_score`` for alerted patients.

    Trang: matplotlib hist(), 20 bins, overlay vertical lines at the
    severity thresholds (0.40 / 0.65 / 0.85).
    """
    from matplotlib import pyplot as plt

    scores = [float(alert.get("anomaly_score", 0.0)) for alert in alerts]

    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=160)
    ax.hist(scores, bins=20, color="#0f766e", edgecolor="white")
    for threshold, label, color in [
        (0.40, "warning", "#d97706"),
        (0.65, "advisory", "#2563eb"),
        (0.85, "critical", "#b91c1c"),
    ]:
        ax.axvline(threshold, linestyle="--", linewidth=1.5, color=color, label=f"{label} {threshold:.2f}")

    ax.set_title("Anomaly score distribution")
    ax.set_xlabel("Anomaly score")
    ax.set_ylabel("Alerts")
    ax.legend(fontsize=8)
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)
    return output


def render_top_patients_table(alerts: list[dict], output: Path) -> Path:
    """A 5-row markdown table of patients with the most critical alerts.

    Trang: write to a ``.md`` file (not PNG) so it can be embedded directly
    in the report.
    """
    critical_alerts = [alert for alert in alerts if str(alert.get("severity", "")).lower() == "critical"]
    grouped: dict[str, list[dict]] = defaultdict(list)
    for alert in critical_alerts:
        grouped[str(alert.get("patient_id", "unknown"))].append(alert)

    ranked = sorted(
        grouped.items(),
        key=lambda item: (len(item[1]), max(float(alert.get("anomaly_score", 0.0)) for alert in item[1])),
        reverse=True,
    )[:5]

    lines = [
        "# Top patients by critical alert count",
        "",
        "| patient_id | critical_alerts | max_score | latest_alert_time |",
        "| --- | ---: | ---: | --- |",
    ]
    for patient_id, patient_alerts in ranked:
        latest_alert = max(
            patient_alerts,
            key=lambda alert: _normalise_alert_time(alert.get("alert_time")) or datetime.min,
        )
        lines.append(
            f"| {patient_id} | {len(patient_alerts)} | {max(float(alert.get('anomaly_score', 0.0)) for alert in patient_alerts):.2f} | {latest_alert.get('alert_time', '')} |"
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--cassandra", default="localhost")
    p.add_argument("--since-minutes", type=int, default=60)
    p.add_argument("--output", type=Path, default=Path("artifacts/demo/figures"))
    p.add_argument("--alerts-export", type=Path, default=DEFAULT_ALERTS_EXPORT)
    return p


def main() -> None:
    args = build_parser().parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    try:
        from brainwatch.serving import cassandra_sink

        try:
            session = cassandra_sink.get_session([args.cassandra])
        except Exception:
            session = None
    except Exception:
        session = None

    global DEFAULT_ALERTS_EXPORT
    DEFAULT_ALERTS_EXPORT = args.alerts_export

    alerts = fetch_alerts(session, args.since_minutes)

    outputs = [
        render_severity_histogram(alerts, args.output / "severity_histogram.png"),
        render_alert_timeline(alerts, args.output / "alert_timeline.png"),
        render_signal_quality_distribution(alerts, args.output / "anomaly_score_distribution.png"),
        render_top_patients_table(alerts, args.output / "top_patients.md"),
    ]

    for path in outputs:
        print(f"wrote {path}")


if __name__ == "__main__":
    main()

import React from "react";

function pct(part, total) {
  if (!total) return "0%";
  return `${((part / total) * 100).toFixed(1)}%`;
}

function MetricCard({ label, value, hint, tone = "flat" }) {
  return (
    <div className="card span-3">
      <div className="metric">
        <span className="metric-label">{label}</span>
        <span className="metric-value">{value}</span>
        {hint && <span className={`metric-delta ${tone}`}>{hint}</span>}
      </div>
    </div>
  );
}

export default function MetricsRow({ summary }) {
  const total = summary.total || 0;
  const critical = summary.bySeverity.critical || 0;
  const warning = summary.bySeverity.warning || 0;
  const advisory = summary.bySeverity.advisory || 0;
  const avgScore = total ? summary.meanScore.toFixed(2) : "—";

  return (
    <>
      <MetricCard
        label="Total alerts"
        value={total.toLocaleString()}
        hint={`${summary.uniquePatients.toLocaleString()} patients`}
        tone="flat"
      />
      <MetricCard
        label="Critical"
        value={critical.toLocaleString()}
        hint={`${pct(critical, total)} of total`}
        tone={critical > 0 ? "down" : "flat"}
      />
      <MetricCard
        label="Warning + Advisory"
        value={(warning + advisory).toLocaleString()}
        hint={`${pct(warning + advisory, total)} of total`}
        tone="flat"
      />
      <MetricCard
        label="Mean anomaly score"
        value={avgScore}
        hint={`max ${summary.maxScore.toFixed(2)}`}
        tone="flat"
      />
    </>
  );
}

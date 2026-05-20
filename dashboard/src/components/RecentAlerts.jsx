import React, { useMemo } from "react";

function pickRecent(alerts, n = 25) {
  return [...alerts]
    .sort((a, b) => (a.alert_time < b.alert_time ? 1 : -1))
    .slice(0, n);
}

function truncate(id) {
  if (!id) return "";
  return id.length > 26 ? id.slice(0, 26) + "…" : id;
}

export default function RecentAlerts({ alerts }) {
  const rows = useMemo(() => pickRecent(alerts), [alerts]);

  if (!rows.length) {
    return <div className="empty-state">no alerts yet</div>;
  }

  return (
    <div className="table-wrap">
      <table className="alert-table">
        <thead>
          <tr>
            <th>Time</th>
            <th>Patient</th>
            <th>Severity</th>
            <th>Score</th>
            <th>Critical lab</th>
            <th>EEG chunks</th>
            <th>Explanation</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={`${r.patient_id}-${r.alert_time}-${i}`}>
              <td>{new Date(r.alert_time).toLocaleString()}</td>
              <td title={r.patient_id}>{truncate(r.patient_id)}</td>
              <td>
                <span className={`severity-tag ${r.severity}`}>{r.severity}</span>
              </td>
              <td>{Number(r.anomaly_score).toFixed(3)}</td>
              <td>{r.has_critical_lab ? "yes" : "—"}</td>
              <td>{r.n_eeg_chunks ?? "—"}</td>
              <td style={{ color: "var(--text-secondary)", maxWidth: 320, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                {r.explanation || ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

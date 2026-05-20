import React, { useMemo } from "react";

const WEIGHT = { critical: 3, warning: 2, advisory: 1, normal: 0, suppressed: 0 };

function buildTop(alerts, limit = 7) {
  const agg = new Map();
  for (const a of alerts) {
    const row = agg.get(a.patient_id) || {
      patient_id: a.patient_id,
      total: 0,
      critical: 0, warning: 0, advisory: 0,
      max_score: 0, latest: "",
    };
    row.total += 1;
    if (a.severity in WEIGHT) row[a.severity] = (row[a.severity] || 0) + (WEIGHT[a.severity] ? 1 : 0);
    const s = Number(a.anomaly_score);
    if (Number.isFinite(s) && s > row.max_score) row.max_score = s;
    if (a.alert_time > row.latest) row.latest = a.alert_time;
    agg.set(a.patient_id, row);
  }

  const ranked = [...agg.values()].map((r) => {
    r.risk = WEIGHT.critical * (r.critical || 0)
           + WEIGHT.warning  * (r.warning  || 0)
           + WEIGHT.advisory * (r.advisory || 0);
    return r;
  });
  ranked.sort((a, b) => b.risk - a.risk || b.max_score - a.max_score);
  return ranked.slice(0, limit);
}

function truncate(id) {
  if (!id) return "";
  return id.length > 22 ? id.slice(0, 22) + "…" : id;
}

export default function TopPatients({ alerts }) {
  const rows = useMemo(() => buildTop(alerts), [alerts]);

  if (!rows.length) {
    return <div className="empty-state">no patients to rank</div>;
  }

  return (
    <div className="table-wrap">
      <table className="alert-table">
        <thead>
          <tr>
            <th>Patient</th>
            <th>Risk</th>
            <th>Critical</th>
            <th>Warning</th>
            <th>Max score</th>
            <th>Latest alert</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.patient_id}>
              <td title={r.patient_id}>{truncate(r.patient_id)}</td>
              <td><strong>{r.risk}</strong></td>
              <td>{r.critical || 0}</td>
              <td>{r.warning || 0}</td>
              <td>{r.max_score.toFixed(2)}</td>
              <td style={{ color: "var(--text-secondary)" }}>
                {r.latest ? new Date(r.latest).toLocaleString() : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

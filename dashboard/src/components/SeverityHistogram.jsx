import React from "react";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell,
} from "recharts";

const SEVERITY_ORDER = ["critical", "warning", "advisory", "normal", "suppressed"];
const COLOR = {
  critical:   "#ef4444",
  warning:    "#f59e0b",
  advisory:   "#3b82f6",
  normal:     "#22c55e",
  suppressed: "#64748b",
};

export default function SeverityHistogram({ summary }) {
  const data = SEVERITY_ORDER.map((sev) => ({
    severity: sev,
    count: summary.bySeverity[sev] || 0,
  }));

  return (
    <div className="chart-shell">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 6, right: 12, left: -10, bottom: 4 }}>
          <XAxis
            dataKey="severity"
            tick={{ fill: "var(--text-secondary)", fontSize: 11 }}
            axisLine={{ stroke: "var(--border-soft)" }}
            tickLine={false}
          />
          <YAxis
            tick={{ fill: "var(--text-secondary)", fontSize: 11 }}
            axisLine={{ stroke: "var(--border-soft)" }}
            tickLine={false}
            width={42}
          />
          <Tooltip
            cursor={{ fill: "rgba(56, 189, 248, 0.05)" }}
            formatter={(v) => v.toLocaleString()}
          />
          <Bar dataKey="count" radius={[6, 6, 0, 0]}>
            {data.map((d) => (
              <Cell key={d.severity} fill={COLOR[d.severity]} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

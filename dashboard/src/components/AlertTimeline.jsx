import React, { useMemo } from "react";
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Legend,
} from "recharts";

const BUCKET_MIN = 5;
const SEVERITIES = ["critical", "warning", "advisory", "normal"];
const COLOR = {
  critical: "#ef4444",
  warning:  "#f59e0b",
  advisory: "#3b82f6",
  normal:   "#22c55e",
};

function bucketize(alerts) {
  if (!alerts.length) return [];
  const buckets = new Map();
  for (const a of alerts) {
    if (a.severity === "suppressed") continue;
    const t = new Date(a.alert_time);
    if (Number.isNaN(t.getTime())) continue;
    const bucketKey = Math.floor(t.getTime() / (BUCKET_MIN * 60_000));
    const row = buckets.get(bucketKey) || {
      t: bucketKey * BUCKET_MIN * 60_000,
      critical: 0, warning: 0, advisory: 0, normal: 0,
    };
    row[a.severity] = (row[a.severity] || 0) + 1;
    buckets.set(bucketKey, row);
  }
  return [...buckets.values()].sort((a, b) => a.t - b.t);
}

function formatTick(t) {
  const d = new Date(t);
  return `${d.getHours().toString().padStart(2, "0")}:${d.getMinutes().toString().padStart(2, "0")}`;
}

export default function AlertTimeline({ alerts }) {
  const data = useMemo(() => bucketize(alerts), [alerts]);

  if (!data.length) {
    return <div className="empty-state">no alerts in window</div>;
  }

  return (
    <div className="chart-shell">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 6, right: 12, left: -10, bottom: 4 }}>
          <defs>
            {SEVERITIES.map((sev) => (
              <linearGradient id={`grad-${sev}`} key={sev} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={COLOR[sev]} stopOpacity={0.7} />
                <stop offset="100%" stopColor={COLOR[sev]} stopOpacity={0.05} />
              </linearGradient>
            ))}
          </defs>
          <CartesianGrid stroke="var(--border-soft)" strokeDasharray="3 3" vertical={false} />
          <XAxis
            dataKey="t"
            tickFormatter={formatTick}
            tick={{ fill: "var(--text-secondary)", fontSize: 11 }}
            axisLine={{ stroke: "var(--border-soft)" }}
            tickLine={false}
            minTickGap={48}
          />
          <YAxis
            tick={{ fill: "var(--text-secondary)", fontSize: 11 }}
            axisLine={{ stroke: "var(--border-soft)" }}
            tickLine={false}
            width={42}
          />
          <Tooltip
            labelFormatter={(t) => new Date(t).toLocaleString()}
            cursor={{ stroke: "var(--accent)", strokeOpacity: 0.4 }}
          />
          <Legend
            iconType="circle"
            wrapperStyle={{ fontSize: 11, color: "var(--text-secondary)" }}
          />
          {SEVERITIES.map((sev) => (
            <Area
              key={sev}
              type="monotone"
              dataKey={sev}
              stackId="1"
              stroke={COLOR[sev]}
              strokeWidth={1.5}
              fill={`url(#grad-${sev})`}
            />
          ))}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

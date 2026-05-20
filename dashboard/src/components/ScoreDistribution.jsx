import React, { useMemo } from "react";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell, ReferenceLine,
} from "recharts";

const BINS = 25;

function severityForScore(s) {
  if (s >= 0.85) return "critical";
  if (s >= 0.65) return "warning";
  if (s >= 0.40) return "advisory";
  return "normal";
}

const COLOR = {
  critical: "#ef4444",
  warning:  "#f59e0b",
  advisory: "#3b82f6",
  normal:   "#22c55e",
};

export default function ScoreDistribution({ alerts }) {
  const data = useMemo(() => {
    const counts = Array.from({ length: BINS }, (_, i) => ({
      binStart: i / BINS,
      binEnd: (i + 1) / BINS,
      score: ((i + 0.5) / BINS).toFixed(2),
      count: 0,
    }));
    for (const a of alerts) {
      const s = Number(a.anomaly_score);
      if (!Number.isFinite(s)) continue;
      const idx = Math.min(BINS - 1, Math.max(0, Math.floor(s * BINS)));
      counts[idx].count += 1;
    }
    return counts;
  }, [alerts]);

  return (
    <div className="chart-shell">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 6, right: 12, left: -10, bottom: 4 }}>
          <XAxis
            dataKey="score"
            tick={{ fill: "var(--text-secondary)", fontSize: 11 }}
            axisLine={{ stroke: "var(--border-soft)" }}
            tickLine={false}
            interval={2}
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
            labelFormatter={(s) => `score ≈ ${s}`}
          />
          <ReferenceLine x="0.40" stroke="#3b82f6" strokeDasharray="3 3" strokeOpacity={0.6} />
          <ReferenceLine x="0.62" stroke="#f59e0b" strokeDasharray="3 3" strokeOpacity={0.6} />
          <ReferenceLine x="0.86" stroke="#ef4444" strokeDasharray="3 3" strokeOpacity={0.6} />
          <Bar dataKey="count" radius={[4, 4, 0, 0]}>
            {data.map((d, i) => (
              <Cell key={i} fill={COLOR[severityForScore(d.binStart)]} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

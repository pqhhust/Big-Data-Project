import React from "react";

function formatTime(d) {
  if (!d) return "—";
  return d.toLocaleTimeString(undefined, { hour12: false });
}

export default function Header({ updatedAt, totalAlerts }) {
  return (
    <header className="app-header">
      <div className="brand">
        <div className="brand-mark">BW</div>
        <div className="brand-text">
          <h1>BrainWatch</h1>
          <small>EEG · EHR · Anomaly Monitor</small>
        </div>
      </div>

      <div className="header-meta">
        <span className="status-dot">Pipeline live</span>
        <span style={{ fontFamily: "JetBrains Mono, monospace" }}>
          {totalAlerts.toLocaleString()} alerts
        </span>
        <span style={{ color: "var(--text-muted)" }}>
          updated {formatTime(updatedAt)}
        </span>
      </div>
    </header>
  );
}

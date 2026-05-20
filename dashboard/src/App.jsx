import React, { useEffect, useMemo, useState } from "react";
import Header from "./components/Header.jsx";
import MetricsRow from "./components/MetricsRow.jsx";
import SeverityHistogram from "./components/SeverityHistogram.jsx";
import AlertTimeline from "./components/AlertTimeline.jsx";
import ScoreDistribution from "./components/ScoreDistribution.jsx";
import TopPatients from "./components/TopPatients.jsx";
import RecentAlerts from "./components/RecentAlerts.jsx";
import { loadAlerts, computeSummary } from "./data/loadAlerts.js";

const REFRESH_MS = 3_000;

export default function App() {
  const [alerts, setAlerts] = useState([]);
  const [updatedAt, setUpdatedAt] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;

    const fetchOnce = async () => {
      try {
        const rows = await loadAlerts();
        if (cancelled) return;
        setAlerts(rows);
        setUpdatedAt(new Date());
        setError(null);
      } catch (e) {
        if (!cancelled) setError(e.message || String(e));
      }
    };

    fetchOnce();
    const id = setInterval(fetchOnce, REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const summary = useMemo(() => computeSummary(alerts), [alerts]);

  return (
    <div className="app-shell">
      <Header updatedAt={updatedAt} totalAlerts={alerts.length} />

      <main className="app-main">
        {error && (
          <section className="card span-12">
            <div className="empty-state">
              Failed to load <code>artifacts/demo/alerts_export.jsonl</code> — {error}
            </div>
          </section>
        )}

        <MetricsRow summary={summary} />

        <section className="card span-8">
          <div className="card-header">
            <h2 className="card-title">Alert Timeline</h2>
            <span className="card-subtitle">stacked by severity · 5-min buckets</span>
          </div>
          <AlertTimeline alerts={alerts} />
        </section>

        <section className="card span-4">
          <div className="card-header">
            <h2 className="card-title">Severity Distribution</h2>
            <span className="card-subtitle">all alerts</span>
          </div>
          <SeverityHistogram summary={summary} />
        </section>

        <section className="card span-6">
          <div className="card-header">
            <h2 className="card-title">Anomaly Score Distribution</h2>
            <span className="card-subtitle">classify_v2 bands</span>
          </div>
          <ScoreDistribution alerts={alerts} />
        </section>

        <section className="card span-6">
          <div className="card-header">
            <h2 className="card-title">Top Patients · Risk Index</h2>
            <span className="card-subtitle">critical + warning weighted</span>
          </div>
          <TopPatients alerts={alerts} />
        </section>

        <section className="card span-12">
          <div className="card-header">
            <h2 className="card-title">Most Recent Alerts</h2>
            <span className="card-subtitle">last 25 · sorted by alert_time desc</span>
          </div>
          <RecentAlerts alerts={alerts} />
        </section>

        <footer className="app-footer">
          <span>BrainWatch · IT4043E capstone · {alerts.length.toLocaleString()} alerts loaded</span>
          <span>refresh every {REFRESH_MS / 1000}s</span>
        </footer>
      </main>
    </div>
  );
}

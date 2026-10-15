import { Feedback, LoadingState } from "../../components/ui/Feedback";
import { useMetrics } from "./useMetrics";

function metricLabel(value: string) {
  return value.replace(/[_-]+/g, " ").replace(/\b\w/g, (character) => character.toUpperCase());
}

function MetricsIcon({ name }: { name: "activity" | "warning" | "percent" }) {
  if (name === "warning") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="m10.3 3.8-8 14A2 2 0 0 0 4 20.8h16a2 2 0 0 0 1.7-3l-8-14a2 2 0 0 0-3.4 0Z" />
        <path d="M12 9v4m0 3h.01" />
      </svg>
    );
  }
  if (name === "percent") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="m7 17 10-10" />
        <circle cx="7" cy="7" r="2.25" />
        <circle cx="17" cy="17" r="2.25" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M4 19V5m0 14h16" />
      <path d="m7 15 3-4 3 2 5-7" />
      <path d="M15 6h3v3" />
    </svg>
  );
}

export function MetricsDashboard() {
  const { metrics, breaches, error, loading } = useMetrics();
  const totalRequests = metrics.reduce((total, metric) => total + metric.count, 0);
  const totalBreaches = metrics.reduce((total, metric) => total + metric.breaches, 0);
  const averageBreachRate = metrics.length
    ? metrics.reduce((total, metric) => total + metric.breach_rate, 0) / metrics.length
    : 0;

  return (
    <section className="card feature-card metrics-card">
      <div className="metrics-heading">
        <div className="feature-card-heading">
          <p className="feature-eyebrow">System health</p>
          <h1>Performance overview</h1>
          <p className="feature-description">
            Monitor response times and threshold compliance across the workspace.
          </p>
        </div>
        <div className="metrics-refresh">
          <span className="metrics-live-dot" aria-hidden="true" />
          <span>Auto-refreshes every 60 seconds</span>
        </div>
      </div>
      <Feedback message={error} />
      {loading ? <LoadingState label="Loading performance metrics…" /> : (
        <>
          <div className="metrics-summary" aria-label="Performance summary">
            <article className="metrics-summary-card">
              <span className="metrics-summary-icon metrics-summary-icon-teal"><MetricsIcon name="activity" /></span>
              <div><small>Total measurements</small><strong>{totalRequests.toLocaleString()}</strong></div>
              <span className="metrics-summary-note">All operations</span>
            </article>
            <article className="metrics-summary-card">
              <span className="metrics-summary-icon metrics-summary-icon-pink"><MetricsIcon name="warning" /></span>
              <div><small>Threshold breaches</small><strong>{totalBreaches.toLocaleString()}</strong></div>
              <span className="metrics-summary-note">Across metrics</span>
            </article>
            <article className="metrics-summary-card">
              <span className="metrics-summary-icon metrics-summary-icon-purple"><MetricsIcon name="percent" /></span>
              <div><small>Average breach rate</small><strong>{averageBreachRate.toFixed(1)}%</strong></div>
              <span className="metrics-summary-note">Current period</span>
            </article>
          </div>
          <div className="metrics-section-heading">
            <div><h2>Latency by operation</h2><p>Percentile timings are shown in milliseconds.</p></div>
            <span className="metrics-target">Lower is better</span>
          </div>
          <div className="responsive-table">
            <table className="feature-table">
              <thead><tr>{["Operation", "Count", "p50", "p90", "p99", "Breaches", "Breach rate"].map((heading) => <th key={heading}>{heading}{heading.startsWith("p") && <span className="table-unit">ms</span>}</th>)}</tr></thead>
              <tbody>
                {metrics.map((metric) => (
                  <tr key={metric.metric_type}>
                    <td><strong>{metricLabel(metric.metric_type)}</strong><small className="metric-key">{metric.metric_type}</small></td>
                    <td>{metric.count.toLocaleString()}</td><td>{metric.p50}</td><td>{metric.p90}</td><td>{metric.p99}</td>
                    <td><span className={metric.breaches ? "metric-breach" : "metric-clear"}>{metric.breaches}</span></td>
                    <td><span className={metric.breach_rate ? "metric-rate metric-rate-warning" : "metric-rate"}>{metric.breach_rate.toFixed(1)}%</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!metrics.length && <p className="empty-state">No measurements available.</p>}
          <div className="breaches-heading"><div><h2>Recent breaches</h2><p>Operations that exceeded their target threshold.</p></div><span>{breaches.length} total</span></div>
          {breaches.length ? <div className="breach-list">{breaches.map((breach, index) => <p key={`${breach.measured_at}-${index}`}><strong>{breach.metric_type}</strong><span>{breach.duration_ms} ms / {breach.threshold_ms} ms</span><small>{new Date(breach.measured_at).toLocaleString()}</small></p>)}</div> : <p className="empty-state">No recent threshold breaches.</p>}
        </>
      )}
    </section>
  );
}

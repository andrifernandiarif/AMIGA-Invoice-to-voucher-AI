import { Link } from "react-router-dom";
export { PasswordInput } from "./ui/PasswordInput";
export { PasswordForm } from "../features/auth/PasswordForm";
export { RecordDetails } from "../features/history/RecordDetails";
export { HistoryTimeline } from "../features/history/HistoryTimeline";
export { AllocationPanel } from "../features/allocation/AllocationPanel";
export { MetricsDashboard } from "../features/metrics/MetricsDashboard";
export { ClientManager } from "../features/clients/ClientManager";
import { RecordDetails } from "../features/history/RecordDetails";

export function ValidationWarnings({
  issues,
}: {
  issues: { flag_type: string; details: unknown }[];
}) {
  return (
    <section className="card feature-card">
      <div className="feature-card-heading">
        <p className="feature-eyebrow">Review</p>
        <h2>Validation warnings</h2>
        <p className="feature-description">
          Warnings are advisory. Finance can accept and generate a voucher with unresolved issues.
        </p>
      </div>
      {issues.map((issue, index) => (
        <article className="warning-item" key={index}>
          <h3>{issue.flag_type.replace(/[_ .]+/g, " ")}</h3>
          {issue.flag_type === "POSSIBLE_DUPLICATE" &&
            Array.isArray((issue.details as { matches?: unknown[] } | null)?.matches) ? (
            <ul className="duplicate-matches">
              {((issue.details as { matches: { request_id: string; matched_sources?: {vendor_name?: string; invoice_number?: string}[] }[] }).matches).map(match => (
                <li key={match.request_id}>
                  <Link to={`/requests/${match.request_id}`}>Request {match.request_id}</Link>
                  {match.matched_sources?.map((source, i) => <div key={i}>{source.vendor_name || "Vendor unavailable"} · Invoice {source.invoice_number || "reference unavailable"}</div>)}
                </li>
              ))}
            </ul>
          ) : <RecordDetails value={issue.details} />}
        </article>
      ))}
    </section>
  );
}

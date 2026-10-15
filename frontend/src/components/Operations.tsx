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
          <RecordDetails value={issue.details} />
        </article>
      ))}
    </section>
  );
}

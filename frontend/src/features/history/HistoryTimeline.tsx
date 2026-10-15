import { Feedback, LoadingState } from "../../components/ui/Feedback";
import { RecordDetails } from "./RecordDetails";
import { useHistoryTimeline } from "./useHistoryTimeline";

export function HistoryTimeline({ requestId }: { requestId: string }) {
  const { items, nextOffset, error, busy, load } = useHistoryTimeline(requestId);
  return (
    <section className="card feature-card history-card">
      <div className="feature-card-heading">
        <p className="feature-eyebrow">Audit trail</p>
        <h2>Activity & audit history</h2>
        <p className="feature-description">A chronological record of changes and decisions.</p>
      </div>
      <Feedback message={error} />
      {busy && !items.length && <LoadingState label="Loading activity…" />}
      <div className="history-timeline">
        {items.map((event) => (
          <article className="revision" key={event.event_id}>
            <span className="revision-marker" aria-hidden="true" />
            <div>
              <strong>{event.event_type.replace(/_/g, " ")}</strong>
              <small>
                {event.actor_name} ·{" "}
                {new Date(event.occurred_at).toLocaleString("en-GB", { timeZone: "Asia/Jakarta" })} WIB
              </small>
              <RecordDetails value={event.payload} />
            </div>
          </article>
        ))}
      </div>
      {!items.length && !busy && !error && <p className="empty-state">No activity recorded.</p>}
      {nextOffset !== null && (
        <button className="secondary action-button" disabled={busy} onClick={() => load(nextOffset)}>
          {busy ? "Loading…" : "Load activity"}
        </button>
      )}
    </section>
  );
}

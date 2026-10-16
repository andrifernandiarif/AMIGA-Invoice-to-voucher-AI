import { FormEvent, useEffect, useState } from "react";
import { api, Detail, json } from "../../api";
import { Feedback } from "../../components/ui/Feedback";
import { errorMessage } from "../../utils/errors";

type Reviewer = { user_id: string; name: string };

export function AllocationPanel({
  requestId,
  data,
  onSaved,
}: {
  requestId: string;
  data: Detail;
  onSaved: () => void;
}) {
  const [reviewers, setReviewers] = useState<Reviewer[]>([]);
  const [reviewer, setReviewer] = useState(data.assigned_reviewer || "");
  const [due, setDue] = useState(data.due_date || "");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (data.available_reviewers) {
      setReviewers(data.available_reviewers);
      return;
    }
    api<Reviewer[]>(`/requests/${requestId}/reviewers`)
      .then(setReviewers)
      .catch((error) => setMessage(errorMessage(error)));
  }, [requestId, data.available_reviewers]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setMessage("");
    try {
      await api(
        `/requests/${requestId}/allocation`,
        json({ assigned_reviewer: reviewer || null, due_date: due || null }, "PATCH"),
      );
      setMessage("Allocation saved.");
      onSaved();
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card feature-card management-form allocation-form" onSubmit={submit}>
      <div className="feature-card-heading">
        <p className="feature-eyebrow">Workflow</p>
        <h2>Work allocation</h2>
        <p className="feature-description">Assign ownership and set the next delivery target.</p>
      </div>
      <label>
        Responsible reviewer
        <select value={reviewer} onChange={(event) => setReviewer(event.target.value)}>
          <option value="">Unassigned</option>
          {reviewers.map((item) => <option key={item.user_id} value={item.user_id}>{item.name}</option>)}
        </select>
      </label>
      <label>
        Due date (WIB)
        <input type="date" value={due} onChange={(event) => setDue(event.target.value)} />
      </label>
      <button className="primary action-button" disabled={busy}>
        {busy ? "Saving…" : "Save allocation"}
      </button>
      <Feedback message={message} tone={message === "Allocation saved." ? "success" : "error"} />
    </form>
  );
}

import { FormEvent, useState } from "react";
import { api, json } from "../../api";
import { Feedback } from "../../components/ui/Feedback";
import { PasswordInput } from "../../components/ui/PasswordInput";
import { errorMessage } from "../../utils/errors";

export function PasswordForm({
  forced = false,
  onChanged,
}: {
  forced?: boolean;
  onChanged: () => void;
}) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api(
        "/auth/change-password",
        json({ current_password: current, new_password: next }),
      );
      onChanged();
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <form className="card feature-card auth-form" onSubmit={submit}>
      <div className="feature-card-heading">
        <div>
          <p className="feature-eyebrow">Account security</p>
          <h2>{forced ? "Change your temporary password" : "Change password"}</h2>
          <p className="feature-description">
            Use 12–128 characters. After saving, sign in with your new password.
          </p>
        </div>
      </div>
      <Feedback message={error} />
      <label>
        Current password
        <PasswordInput
          autoComplete="current-password"
          required
          maxLength={128}
          placeholder="Enter your current password"
          value={current}
          onChange={setCurrent}
        />
      </label>
      <label>
        New password
        <PasswordInput
          autoComplete="new-password"
          required
          minLength={12}
          maxLength={128}
          placeholder="At least 12 characters"
          value={next}
          onChange={setNext}
        />
      </label>
      <div className="form-footer">
        <button className="primary" disabled={busy}>
          {busy ? "Saving…" : "Change password"}
        </button>
      </div>
    </form>
  );
}

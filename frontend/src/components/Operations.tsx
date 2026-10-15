import { FormEvent, useEffect, useState } from "react";
import { api, Client, Detail, json } from "../api";

export function PasswordInput({
  value,
  onChange,
  autoComplete,
  required = false,
  minLength,
  maxLength,
  ariaLabel,
  placeholder,  
  
}: {
  value: string;
  onChange: (value: string) => void;
  autoComplete: string;
  required?: boolean;
  minLength?: number;
  maxLength?: number;
  ariaLabel?: string;
  placeholder?: string; 
}) {
  const [visible, setVisible] = useState(false);
  return (
    <span className="password-input">
      <input
        type={visible ? "text" : "password"}
        autoComplete={autoComplete}
        aria-label={ariaLabel}
        required={required}
        minLength={minLength}
        maxLength={maxLength}
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
      <button
        type="button"
        className="password-visibility"
        aria-label={visible ? "Hide password" : "Show password"}
        onClick={() => setVisible((current) => !current)}
      >
        <svg
          width="17"
          height="17"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.7"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          {visible ? (
            <>
              <path d="m3 3 18 18" />
              <path d="M10.6 10.6a2 2 0 0 0 2.8 2.8" />
              <path d="M9.9 4.3A10.8 10.8 0 0 1 12 4c5.5 0 9.5 5 9.5 5s-1.2 1.5-3.2 2.9M6.2 6.2C3.8 7.7 2.5 9 2.5 9s4 5 9.5 5c.7 0 1.3-.1 1.9-.2" />
            </>
          ) : (
            <>
              <path d="M2.5 9s4-5 9.5-5 9.5 5 9.5 5-4 5-9.5 5-9.5-5-9.5-5Z" />
              <circle cx="12" cy="9" r="2" />
            </>
          )}
        </svg>
      </button>
    </span>
  );
}

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
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api(
        "/auth/change-password",
        json({ current_password: current, new_password: next }),
      );
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <form className="card" onSubmit={submit}>
      <h2>{forced ? "Change your temporary password" : "Change password"}</h2>
      <p>
        Use 12–128 characters. After saving, sign in with your new password.
      </p>
      {error && <p role="alert">{error}</p>}
      <label>
        Current password
        <PasswordInput
          autoComplete="current-password"
          required
          maxLength={128}
          placeholder="****"
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
          value={next}
          onChange={setNext}
        />
      </label>
       <div className="form-footer">
            <button className="primary" disabled={busy}>
             Change Password
            </button>
          </div>
    </form>
  );
}

function humanLabel(value: string) {
  return value.replace(/[_ .]+/g, " ").replace(/^./, (c) => c.toUpperCase());
}

export function RecordDetails({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <span>Empty</span>;
  if (Array.isArray(value))
    return (
      <ul>
        {value.map((item, index) => (
          <li key={index}>
            <RecordDetails value={item} />
          </li>
        ))}
      </ul>
    );
  if (typeof value === "object")
    return (
      <dl>
        {Object.entries(value as Record<string, unknown>)
          .filter(
            ([key]) =>
              !key.endsWith("_id") &&
              !key.includes("file_key") &&
              !key.includes("hash"),
          )
          .map(([key, item]) => (
            <div key={key}>
              <dt>{humanLabel(key)}</dt>
              <dd>
                <RecordDetails value={item} />
              </dd>
            </div>
          ))}
      </dl>
    );
  return <span>{String(value)}</span>;
}

export function ValidationWarnings({
  issues,
}: {
  issues: { flag_type: string; details: unknown }[];
}) {
  return (
    <section className="card">
      <h2>Validation warnings</h2>
      <p>
        Warnings are advisory. Finance can accept and generate a voucher with
        unresolved issues.
      </p>
      {issues.map((issue, index) => (
        <article key={index}>
          <h3>{humanLabel(issue.flag_type.toLowerCase())}</h3>
          <RecordDetails value={issue.details} />
        </article>
      ))}
    </section>
  );
}

type Event = {
  event_id: string;
  event_type: string;
  actor_name: string;
  occurred_at: string;
  payload: unknown;
};
export function HistoryTimeline({ requestId }: { requestId: string }) {
  const [items, setItems] = useState<Event[]>([]);
  const [offset, setOffset] = useState<number | null>(0);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function load(start: number) {
    setBusy(true);
    setError("");
    try {
      const data = await api<{ items: Event[]; next_offset: number | null }>(
        `/requests/${requestId}/history?offset=${start}`,
      );
      setItems((old) => (start ? [...old, ...data.items] : data.items));
      setOffset(data.next_offset);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    setItems([]);
    void load(0);
  }, [requestId]);
  return (
    <section className="card">
      <h2>Activity & audit history</h2>
      {error && <p role="alert">{error}</p>}
      {items.map((e) => (
        <article className="revision" key={e.event_id}>
          <strong>{e.event_type.replace(/_/g, " ")}</strong>
          <small>
            {e.actor_name} ·{" "}
            {new Date(e.occurred_at).toLocaleString("en-GB", {
              timeZone: "Asia/Jakarta",
            })}{" "}
            WIB
          </small>
          <RecordDetails value={e.payload} />
        </article>
      ))}
      {!items.length && !busy && !error && <p>No activity recorded.</p>}
      {offset !== null && (
        <button disabled={busy} onClick={() => load(offset)}>
          {busy ? "Loading…" : "Load activity"}
        </button>
      )}
    </section>
  );
}

export function AllocationPanel({
  requestId,
  data,
  onSaved,
}: {
  requestId: string;
  data: Detail;
  onSaved: () => void;
}) {
  const [reviewers, setReviewers] = useState<
    { user_id: string; name: string }[]
  >([]);
  const [reviewer, setReviewer] = useState(data.assigned_reviewer || "");
  const [due, setDue] = useState(data.due_date || "");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api<typeof reviewers>(`/requests/${requestId}/reviewers`)
      .then(setReviewers)
      .catch((e) => setMessage(e.message));
  }, [requestId]);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await api(
        `/requests/${requestId}/allocation`,
        json(
          { assigned_reviewer: reviewer || null, due_date: due || null },
          "PATCH",
        ),
      );
      setMessage("Allocation saved.");
      onSaved();
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <form className="card management-form" onSubmit={submit}>
      <h2>Work allocation</h2>
      <label>
        Responsible reviewer
        <select value={reviewer} onChange={(e) => setReviewer(e.target.value)}>
          <option value="">Unassigned</option>
          {reviewers.map((r) => (
            <option key={r.user_id} value={r.user_id}>
              {r.name}
            </option>
          ))}
        </select>
      </label>
      <label>
        Due date (WIB)
        <input
          type="date"
          value={due}
          onChange={(e) => setDue(e.target.value)}
        />
      </label>
      <button disabled={busy}>Save allocation</button>
      <p role="status">{message}</p>
    </form>
  );
}

type Metric = {
  metric_type: string;
  count: number;
  breaches: number;
  breach_rate: number;
  p50: number;
  p90: number;
  p99: number;
};
type Breach = {
  metric_type: string;
  duration_ms: number;
  threshold_ms: number;
  measured_at: string;
};
export function MetricsDashboard() {
  const [metrics, setMetrics] = useState<Metric[]>([]);
  const [breaches, setBreaches] = useState<Breach[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    const load = () =>
      Promise.all([
        api<Metric[]>("/telemetry/metrics"),
        api<Breach[]>("/telemetry/breaches"),
      ])
        .then(([m, b]) => {
          if (live) {
            setMetrics(m);
            setBreaches(b);
            setError("");
          }
        })
        .catch((e) => {
          if (live) setError(e.message);
        });
    void load();
    const timer = setInterval(load, 60000);
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, []);
  return (
    <section className="card">
      <h1>Performance</h1>
      <p>
        Measured durations refresh every 60 seconds. Targets: screens ≤2s, PDF
        ≤3s, extraction ≤15s for up to five pages.
      </p>
      {error && <p role="alert">{error}</p>}
      <table>
        <thead>
          <tr>
            {[
              "Operation",
              "Count",
              "p50 ms",
              "p90 ms",
              "p99 ms",
              "Breaches",
              "Breach rate",
            ].map((h) => (
              <th key={h}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {metrics.map((m) => (
            <tr key={m.metric_type}>
              <td>{m.metric_type}</td>
              <td>{m.count}</td>
              <td>{m.p50}</td>
              <td>{m.p90}</td>
              <td>{m.p99}</td>
              <td>{m.breaches}</td>
              <td>{m.breach_rate.toFixed(1)}%</td>
            </tr>
          ))}
        </tbody>
      </table>
      {!metrics.length && <p>No measurements available.</p>}
      <h2>Recent breaches</h2>
      {breaches.map((b, i) => (
        <p key={i}>
          {b.metric_type}: {b.duration_ms} ms / {b.threshold_ms} ms ·{" "}
          {new Date(b.measured_at).toLocaleString()}
        </p>
      ))}
    </section>
  );
}

export function ClientManager() {
  const [clients, setClients] = useState<Client[]>([]);
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [message, setMessage] = useState("");
  const load = () =>
    api<Client[]>("/clients/all")
      .then(setClients)
      .catch((e) => setMessage(e.message));
  useEffect(() => {
    void load();
  }, []);
  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      await api(
        "/clients",
        json({ client_name: name, client_code: code || null }),
      );
      setName("");
      setCode("");
      await load();
      setMessage("Company created.");
    } catch (e) {
      setMessage((e as Error).message);
    }
  }
  return (
    <section className="card">
      <h1>Companies</h1>
      <form className="management-form" onSubmit={submit}>
        <label>
          Company name
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
        </label>
        <label>
          Code (optional)
          <input
            value={code}
            onChange={(e) => setCode(e.target.value.toUpperCase())}
            pattern="[A-Z0-9][A-Z0-9-]{0,39}"
          />
        </label>
        <button>Create company</button>
      </form>
      <p role="status">{message}</p>
      {clients.map((c) => (
        <p key={c.client_id}>
          {c.client_name} · {c.client_code}
        </p>
      ))}
    </section>
  );
}

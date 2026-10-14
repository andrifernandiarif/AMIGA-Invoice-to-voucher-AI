import { FormEvent, useCallback, useEffect, useState } from "react";
import {
  BrowserRouter,
  Link,
  NavLink,
  Route,
  Routes,
  useNavigate,
  useParams,
} from "react-router-dom";
import {
  api,
  apiFetch,
  Client,
  Detail,
  Invoice,
  json,
  statusLabels,
  tokenKey,
  User,
} from "./api";
import TemplateEditor from "./components/TemplateEditor";
import VoucherDocument from "./components/VoucherDocument";

function Icon({ name, size = 20 }: { name: string; size?: number }) {
  const paths: Record<string, string> = {
    grid: "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
    file: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z M14 2v6h6 M8 13h8 M8 17h6",
    check: "m5 12 4 4L19 6",
    upload: "M12 16V3 m-5 5 5-5 5 5 M4 16v4h16v-4",
    arrow: "M5 12h14 m-5-5 5 5-5 5",
    search: "M21 21l-5-5 M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0",
    clock: "M12 8v4l3 2 M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0",
    alert: "M12 9v4 m0 4h.01 M10 3 1 19h22L14 3z",
    logout: "M9 3H3v18h6 M9 12h12 m-4-4 4 4-4 4",
    plus: "M12 5v14 M5 12h14",
  };
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={paths[name] || paths.file} />
    </svg>
  );
}
const label = (s: string) =>
  s
    .replace(/_/g, " ")
    .toLowerCase()
    .replace(/^./, (c) => c.toUpperCase());
const date = (s: string) =>
  new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(new Date(s));
function Badge({ status }: { status: string }) {
  return (
    <span className={`badge ${status.toLowerCase()}`}>
      <i />
      {statusLabels[status] || label(status)}
    </span>
  );
}
function ErrorMessage({ message }: { message: string }) {
  return message ? (
    <div className="error-banner" role="alert">
      <Icon name="alert" />
      {message}
    </div>
  ) : null;
}

function Login({ onLogin }: { onLogin: (user: User) => void }) {
  const [username, setUsername] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await api<{ access_token: string }>("/auth/token", {
        method: "POST",
        body: new URLSearchParams({ username }),
      });
      sessionStorage.setItem(tokenKey, result.access_token);
      onLogin(await api<User>("/auth/me"));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="login-page">
      <div className="login-story">
        <div className="wordmark">
          <span className="brand-icon">a</span>amiga
          <span className="brand-dot">.</span>
        </div>
        <span className="eyebrow">YOUR FINANCE WORKSPACE</span>
        <h1>
          Less paperwork.
          <br />
          More clarity.
        </h1>
        <p>
          Turn vendor invoices into reviewed, ready-to-share vouchers. One
          connected workspace, from upload to approval.
        </p>
        <div className="login-steps">
          <span>01 · Upload</span>
          <span>02 · Review</span>
          <span>03 · Deliver</span>
        </div>
      </div>
      <form className="login-form" onSubmit={submit}>
        <span className="eyebrow">WELCOME BACK</span>
        <h2>Sign in to your workspace</h2>
        <p>Use your existing account to get started.</p>
        <ErrorMessage message={error} />
        <label>
          Username
          <input
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="Enter your username"
            required
          />
        </label>
        <button className="primary" disabled={busy}>
          {busy ? "Signing in…" : "Continue"}
          <Icon name="arrow" />
        </button>
        <div className="dev-note">
          Development authentication is enabled. Your backend currently signs in
          existing users by username without a password.
        </div>
      </form>
    </div>
  );
}

function Dashboard({
  user,
  vouchers = false,
}: {
  user: User;
  vouchers?: boolean;
}) {
  const [items, setItems] = useState<Invoice[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("all");
  const load = useCallback(async () => {
    try {
      setItems(await api<Invoice[]>("/requests"));
      setError("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    void load();
    const timer = setInterval(load, 10000);
    return () => clearInterval(timer);
  }, [load]);
  const approved = items.filter((i) =>
    ["AMIGA_REVIEWED", "SENT_TO_CLIENT"].includes(i.status),
  );
  const source = vouchers ? approved : items;
  const visible = source.filter(
    (i) =>
      (filter === "all" || i.status === filter) &&
      `${i.client_name} ${i.request_id}`
        .toLowerCase()
        .includes(search.toLowerCase()),
  );
  const canUpload = ["CLIENT_PIC", "INTAKE_SUPPORT"].includes(user.role);
  return (
    <>
      <div className="page-heading">
        <div>
          <span className="eyebrow">
            WORKSPACE / {vouchers ? "VOUCHERS" : "OVERVIEW"}
          </span>
          <h1>
            {vouchers ? "Your vouchers" : "Invoice overview"}
            <span className="heading-dot">.</span>
          </h1>
          <p>
            {vouchers
              ? "Reviewed invoices, ready for the next step."
              : "A clear view of your invoices, from intake to delivery."}
          </p>
        </div>
        {canUpload && (
          <Link className="primary" to="/upload">
            <Icon name="plus" />
            New request
          </Link>
        )}
      </div>
      {!vouchers && (
        <div className="stats">
          {[
            {
              title: "Total requests",
              value: items.length,
              icon: "file",
              note: "Across your assigned clients",
              tone: "purple",
            },
            {
              title: "Ready for review",
              value: items.filter((i) => i.status === "READY_FOR_REVIEW")
                .length,
              icon: "clock",
              note: "Awaiting independent review",
              tone: "blue",
            },
            {
              title: "Processing",
              value: items.filter((i) =>
                ["QUEUED", "PROCESSING"].includes(i.status),
              ).length,
              icon: "clock",
              note: "Preparing extracted drafts",
              tone: "amber",
            },
            {
              title: "Approved vouchers",
              value: approved.length,
              icon: "check",
              note: "Reviewed and ready to deliver",
              tone: "green",
            },
          ].map((s) => (
            <div className="stat" key={s.title}>
              <div className="stat-top">
                {s.title}
                <span className={`stat-icon ${s.tone}`}>
                  <Icon name={s.icon} />
                </span>
              </div>
              <strong>{loading ? "—" : s.value}</strong>
              <small>{s.note}</small>
            </div>
          ))}
        </div>
      )}
      {!vouchers && (
        <div className="workflow-banner">
          <div className="workflow-symbol">
            <Icon name="file" size={28} />
          </div>
          <div>
            <h3>From invoice to voucher. All in one place.</h3>
            <p>
              Upload your evidence. Let AI prepare the draft. Review with
              confidence.
            </p>
          </div>
          <div className="workflow-trail">
            <span>Upload</span>
            <Icon name="arrow" size={15} />
            <span>Review</span>
            <Icon name="arrow" size={15} />
            <span>Voucher</span>
          </div>
        </div>
      )}
      <ErrorMessage message={error} />
      <section className="request-panel">
        <div className="panel-heading">
          <div>
            <h2>
              {vouchers ? "Approved requests" : "All requests"}{" "}
              <span className="count">{source.length}</span>
            </h2>
            <p>
              {vouchers
                ? "Open a request to preview or download its voucher."
                : "Track progress and pick up where you left off."}
            </p>
          </div>
          <button className="text-button" onClick={load}>
            Refresh ↻
          </button>
        </div>
        <div className="table-tools">
          <div className="tabs">
            {["all", "READY_FOR_REVIEW", "PROCESSING", "AMIGA_REVIEWED"].map(
              (s) => (
                <button
                  key={s}
                  className={filter === s ? "active" : ""}
                  onClick={() => setFilter(s)}
                >
                  {s === "all" ? "All requests" : statusLabels[s]}
                </button>
              ),
            )}
          </div>
          <label className="search">
            <Icon name="search" size={17} />
            <input
              aria-label="Search requests"
              placeholder="Search requests…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </label>
        </div>
        <div className="table-scroll">
          <table className="requests-table">
            <thead>
              <tr>
                <th>Request</th>
                <th>Client</th>
                <th>Submitted</th>
                <th>Status</th>
                <th aria-label="Open request" />
              </tr>
            </thead>
            <tbody>
              {visible.map((i) => (
                <tr key={i.request_id}>
                  <td>
                    <Link
                      className="request-link"
                      to={`/requests/${i.request_id}`}
                    >
                      <span className="document-icon">
                        <Icon name="file" size={18} />
                      </span>
                      <span>
                        INV-{i.request_id.slice(0, 8).toUpperCase()}
                        <small>{label(i.channel)}</small>
                      </span>
                    </Link>
                  </td>
                  <td>{i.client_name}</td>
                  <td className="muted">{date(i.created_at)}</td>
                  <td>
                    <Badge status={i.status} />
                  </td>
                  <td>
                    <Link
                      className="open-link"
                      aria-label={`Open request ${i.request_id}`}
                      to={`/requests/${i.request_id}`}
                    >
                      <Icon name="arrow" size={18} />
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!visible.length && (
          <div className="empty-state">
            <span className="empty-icon">
              <Icon name={loading ? "clock" : "file"} size={28} />
            </span>
            <h3>
              {loading
                ? "Loading your requests…"
                : search || filter !== "all"
                  ? "No matching requests"
                  : "A fresh start for your invoices"}
            </h3>
            <p>
              {loading
                ? "Connecting to your workspace."
                : search || filter !== "all"
                  ? "Try another search or status filter."
                  : "Your requests will appear here as invoices are submitted."}
            </p>
            {!loading && canUpload && !search && filter === "all" && (
              <Link className="primary" to="/upload">
                <Icon name="plus" size={17} />
                Upload your first invoice
              </Link>
            )}
          </div>
        )}
        <div className="table-footer">
          <span>
            {visible.length} of {source.length} requests
          </span>
          <span>
            <i className="live-dot" />
            Updates every 10 seconds
          </span>
        </div>
      </section>
      <div className="workspace-note">
        <Icon name="check" size={16} />
        Every approval includes an independent review and an audit trail.
      </div>
    </>
  );
}

function Upload() {
  const navigate = useNavigate();
  const [clients, setClients] = useState<Client[]>([]);
  const [client, setClient] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  useEffect(() => {
    api<Client[]>("/clients")
      .then(setClients)
      .catch((e) => setError(e.message));
  }, []);
  function addFiles(incoming: File[]) {
    if (
      incoming.some(
        (f) => !["application/pdf", "image/png", "image/jpeg"].includes(f.type),
      )
    ) {
      setError("Choose PDF, PNG, or JPEG evidence files.");
      return;
    }
    if (
      incoming.some((f) => f.size > 20 * 1024 * 1024) ||
      incoming.length + files.length > 20
    ) {
      setError("Upload up to 20 files, each under 20 MB.");
      return;
    }
    setFiles((prev) => [...prev, ...incoming]);
    setError("");
  }
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setError("");
    const form = new FormData(e.currentTarget);
    form.delete("files");
    files.forEach((f) => form.append("files", f));
    try {
      const result = await api<{ request_id: string }>("/requests", {
        method: "POST",
        body: form,
      });
      navigate(`/requests/${result.request_id}`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const today = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Jakarta",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
  return (
    <>
      <Link to="/" className="back-link">
        ← Back to overview
      </Link>
      <div className="page-heading">
        <div>
          <span className="eyebrow">WORKSPACE / NEW REQUEST</span>
          <h1>
            Start with an invoice<span className="heading-dot">.</span>
          </h1>
          <p>Upload your evidence and we’ll prepare a draft for review.</p>
        </div>
      </div>
      <form className="upload-layout" onSubmit={submit}>
        <section className="card">
          <h2>Request details</h2>
          <p className="muted">
            Choose the client and invoice date for this request.
          </p>
          <ErrorMessage message={error} />
          <div className="form-grid">
            <label>
              Client
              <select
                required
                name="client_id"
                value={client}
                onChange={(e) => setClient(e.target.value)}
              >
                <option value="">Select a client</option>
                {clients.map((c) => (
                  <option key={c.client_id} value={c.client_id}>
                    {c.client_name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Request date
              <input
                name="request_date"
                type="date"
                required
                defaultValue={today}
              />
            </label>
            <label>
              Request type
              <select name="request_type">
                <option value="standard">Standard invoice</option>
                <option value="reimbursement">Reimbursement</option>
              </select>
            </label>
            <label>
              Submission channel
              <select name="channel">
                <option value="upload">Direct upload</option>
                <option value="email">Email</option>
              </select>
            </label>
          </div>
          <h2 className="section-title">Invoice & supporting evidence</h2>
          <label
            className={`dropzone ${dragging ? "dragging" : ""}`}
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              addFiles(Array.from(e.dataTransfer.files));
            }}
          >
            <span className="empty-icon">
              <Icon name="upload" size={26} />
            </span>
            <strong>
              Drag your files here, or <span>browse files</span>
            </strong>
            <small>
              PDF, PNG or JPEG · Up to 20 MB per file · Maximum 20 files
            </small>
            <input
              type="file"
              accept="application/pdf,image/png,image/jpeg"
              multiple
              aria-label="Choose invoice evidence"
              onChange={(e) => {
                addFiles(Array.from(e.target.files || []));
                e.target.value = "";
              }}
            />
          </label>
          <div className="file-list">
            {files.map((f, i) => (
              <div key={`${f.name}-${i}`}>
                <Icon name="file" />
                <span>
                  {f.name}
                  <small>{(f.size / 1024 / 1024).toFixed(2)} MB</small>
                </span>
                <button
                  type="button"
                  className="text-button"
                  onClick={() =>
                    setFiles((prev) => prev.filter((_, n) => n !== i))
                  }
                  aria-label={`Remove ${f.name}`}
                >
                  ×
                </button>
              </div>
            ))}
          </div>
          <div className="form-footer">
            <Link className="secondary" to="/">
              Cancel
            </Link>
            <button
              className="primary"
              disabled={busy || !files.length || !client}
            >
              {busy ? "Uploading…" : "Submit for extraction"}
              <Icon name="arrow" size={18} />
            </button>
          </div>
        </section>
        <aside className="upload-help">
          <span className="eyebrow">WHAT HAPPENS NEXT</span>
          <h2>A little less admin.</h2>
          {[
            [
              "01",
              "AI prepares your draft",
              "Invoice fields and payment details are extracted from your evidence.",
            ],
            [
              "02",
              "A manual review",
              "A finance reviewer compares the extracted values with your evidence, then accepts or rejects the request.",
            ],
            [
              "03",
              "Your voucher is ready",
              "Preview and download the reviewed voucher for your client.",
            ],
          ].map(([n, h, p]) => (
            <div className="help-step" key={n}>
              <span>{n}</span>
              <div>
                <h3>{h}</h3>
                <p>{p}</p>
              </div>
            </div>
          ))}
          <div className="dev-note">
            AI-generated fields must be reviewed. Payment authorization remains
            with the client.
          </div>
        </aside>
      </form>
    </>
  );
}

function RequestView({ user }: { user: User }) {
  const { requestId } = useParams();
  const [data, setData] = useState<Detail | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [changes, setChanges] = useState<Record<string, string>>({});
  const [reason, setReason] = useState("");
  const [rejecting, setRejecting] = useState(false);
  const [evidenceIndex, setEvidenceIndex] = useState(0);
  const [evidenceUrl, setEvidenceUrl] = useState("");
  const [evidenceError, setEvidenceError] = useState("");
  const [evidenceRetry, setEvidenceRetry] = useState(0);
  const [tab, setTab] = useState("draft");
  const [revisions, setRevisions] = useState<
    {
      revision_id: string;
      field_path: string;
      old_value: string | null;
      new_value: string | null;
      revised_at: string;
    }[]
  >([]);
  const load = useCallback(async () => {
    try {
      setData(await api<Detail>(`/requests/${requestId}`));
    } catch (e) {
      setError((e as Error).message);
    }
  }, [requestId]);
  useEffect(() => {
    void load();
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, [load]);
  useEffect(() => {
    if (!Object.keys(changes).length) return;
    const guard = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [changes]);
  const evidenceKey = data?.evidence_files[evidenceIndex]?.file_key;
  useEffect(() => {
    setEvidenceUrl("");
    setEvidenceError("");
    if (!evidenceKey) return;
    let cancelled = false;
    let url = "";
    apiFetch(`/api/evidence/${encodeURIComponent(evidenceKey)}`)
      .then((r) => r.blob())
      .then((blob) => {
        if (!cancelled) {
          url = URL.createObjectURL(blob);
          setEvidenceUrl(url);
        }
      })
      .catch((e) => {
        if (!cancelled) setEvidenceError(e.message);
      });
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [evidenceKey, evidenceRetry]);
  useEffect(() => {
    if (tab === "history")
      api<typeof revisions>(`/requests/${requestId}/revisions`)
        .then(setRevisions)
        .catch((e) => setError(e.message));
  }, [tab, requestId]);
  async function action(path: string, body?: unknown) {
    setBusy(true);
    setError("");
    try {
      await api(`/requests/${requestId}/${path}`, json(body ?? {}));
      await load();
      setRejecting(false);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function save() {
    setBusy(true);
    setError("");
    try {
      for (const [field_path, new_value] of Object.entries(changes)) {
        await api(
          `/requests/${requestId}/fields`,
          json({ field_path, new_value }, "PUT"),
        );
        setChanges((prev) => {
          const next = { ...prev };
          delete next[field_path];
          return next;
        });
      }
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function download() {
    setBusy(true);
    setError("");
    try {
      const response = await apiFetch(`/api/requests/${requestId}/voucher-pdf`);
      const url = URL.createObjectURL(await response.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = `voucher_${requestId}.pdf`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function downloadEvidence(file: Detail["evidence_files"][number]) {
    setError("");
    try {
      const response = await apiFetch(
        `/api/evidence/${encodeURIComponent(file.file_key)}?download=true`,
      );
      const url = URL.createObjectURL(await response.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = file.original_filename;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  const editable =
    ["FINANCE_REVIEWER", "SYSTEM_ADMIN"].includes(user.role) &&
    !!data &&
    ["READY_FOR_REVIEW", "EXCEPTION"].includes(data.status);
  const dirty = Object.keys(changes).length > 0;
  useEffect(() => {
    if (!dirty) return;
    const guard = (event: MouseEvent) => {
      const anchor = (event.target as Element).closest("a");
      if (
        anchor &&
        anchor.getAttribute("href")?.startsWith("/") &&
        !window.confirm("Discard unsaved changes?")
      ) {
        event.preventDefault();
        event.stopPropagation();
      }
    };
    document.addEventListener("click", guard, true);
    return () => document.removeEventListener("click", guard, true);
  }, [dirty]);
  function field(path: string, value: string | number | null, name: string) {
    return (
      <label key={path}>
        {name}
        <input
          disabled={!editable || busy}
          value={changes[path] ?? value ?? ""}
          onChange={(e) =>
            setChanges((prev) => ({ ...prev, [path]: e.target.value }))
          }
          type={
            path.endsWith("invoice_date") || path.endsWith("item_date")
              ? "date"
              : "text"
          }
        />
        {changes[path] !== undefined && (
          <small className="original-value">
            Before edit: {value ?? "Empty"}
          </small>
        )}
      </label>
    );
  }
  return (
    <>
      <Link to="/" className="back-link">
        ← Back to overview
      </Link>
      <div className="page-heading">
        <div>
          <span className="eyebrow">WORKSPACE / REQUEST DETAIL</span>
          <h1>
            Invoice review<span className="heading-dot">.</span>
          </h1>
          <p>
            INV-{requestId?.slice(0, 8).toUpperCase()} · Check the evidence,
            then take the next step.
          </p>
        </div>
        {data && <Badge status={data.status} />}
      </div>
      <ErrorMessage message={error} />
      {!data ? (
        <div className="card empty-state">
          {error ? "Unable to load this request." : "Loading request…"}
          <button className="secondary" onClick={load}>
            Retry
          </button>
        </div>
      ) : (
        <>
          <div className="detail-toolbar">
            <div className="tabs">
              {[
                "draft",
                ...(["FINANCE_REVIEWER", "CLIENT_PIC"].includes(user.role)
                  ? ["voucher"]
                  : []),
                ...([
                  "FINANCE_REVIEWER",
                  "SERVICE_LEAD",
                  "SYSTEM_ADMIN",
                ].includes(user.role)
                  ? ["history"]
                  : []),
              ].map((t) => (
                <button
                  key={t}
                  className={tab === t ? "active" : ""}
                  onClick={() => setTab(t)}
                >
                  {t === "draft"
                    ? "Invoice draft"
                    : t === "voucher"
                      ? "Voucher preview"
                      : "Revision history"}
                </button>
              ))}
            </div>
            <div className="detail-actions">
              {editable && (
                <button
                  className="secondary"
                  disabled={!dirty || busy}
                  onClick={save}
                >
                  {busy
                    ? "Saving…"
                    : `Save changes${dirty ? ` (${Object.keys(changes).length})` : ""}`}
                </button>
              )}
              {user.role === "FINANCE_REVIEWER" &&
                ["READY_FOR_REVIEW", "EXCEPTION"].includes(data.status) && (
                  <>
                    <button
                      className="danger-outline"
                      disabled={busy || dirty}
                      onClick={() => setRejecting(true)}
                    >
                      Reject
                    </button>
                    <button
                      className="primary"
                      disabled={busy || dirty || !data.draft}
                      onClick={() => action("approve")}
                    >
                      <Icon name="check" size={17} />
                      {busy
                        ? "Generating voucher…"
                        : "Accept & generate voucher"}
                    </button>
                  </>
                )}
              {["CLIENT_PIC", "FINANCE_REVIEWER"].includes(user.role) &&
                ["AMIGA_REVIEWED", "SENT_TO_CLIENT"].includes(data.status) && (
                  <button
                    className="primary"
                    disabled={busy}
                    onClick={download}
                  >
                    <Icon name="upload" size={17} />
                    {busy ? "Preparing…" : "Download voucher"}
                  </button>
                )}
            </div>
          </div>
          {data.rejection_reason && (
            <div className="error-banner">
              Rejection reason: {data.rejection_reason}
            </div>
          )}
          {rejecting && (
            <form
              className="card rejection-form"
              onSubmit={(e) => {
                e.preventDefault();
                void action("reject", { rejection_reason: reason });
              }}
            >
              <label>
                Reason for rejection
                <textarea
                  required
                  maxLength={500}
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                />
              </label>
              <div className="detail-actions">
                <button
                  type="button"
                  className="secondary"
                  onClick={() => setRejecting(false)}
                >
                  Cancel
                </button>
                <button
                  className="danger-outline"
                  disabled={busy || !reason.trim()}
                >
                  Confirm rejection
                </button>
              </div>
            </form>
          )}
          {tab === "draft" && (
            <div className="review-grid">
              <section className="card evidence-card">
                <div className="panel-heading">
                  <h2>Original evidence</h2>
                  <span className="count">
                    {data.evidence_files.length} files
                  </span>
                </div>
                <select
                  aria-label="Select evidence file"
                  value={evidenceIndex}
                  onChange={(e) => setEvidenceIndex(Number(e.target.value))}
                >
                  {data.evidence_files.map((f, i) => (
                    <option value={i} key={f.file_key}>
                      {f.original_filename}
                    </option>
                  ))}
                </select>
                {evidenceUrl ? (
                  data.evidence_files[evidenceIndex].content_type.startsWith(
                    "image/",
                  ) ? (
                    <img
                      className="evidence-image"
                      src={evidenceUrl}
                      alt="Uploaded invoice evidence"
                    />
                  ) : (
                    <iframe title="Original invoice" src={evidenceUrl} />
                  )
                ) : (
                  <div className="empty-state">
                    {evidenceError ? (
                      <>
                        <ErrorMessage message={evidenceError} />
                        <button
                          className="secondary"
                          onClick={() => setEvidenceRetry((n) => n + 1)}
                        >
                          Retry evidence
                        </button>
                      </>
                    ) : data.evidence_files.length ? (
                      "Loading evidence…"
                    ) : (
                      "No evidence attached."
                    )}
                  </div>
                )}
                {evidenceUrl && (
                  <a
                    className="text-button"
                    href={evidenceUrl}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Open evidence in a new tab ↗
                  </a>
                )}
                <div className="evidence-downloads">
                  <h3>Download evidence</h3>
                  {data.evidence_files.map((file) => (
                    <div className="evidence-download-row" key={file.file_key}>
                      <span>
                        <Icon name="file" size={16} />
                        {file.original_filename}
                      </span>
                      <button
                        className="secondary"
                        onClick={() => downloadEvidence(file)}
                      >
                        Download
                      </button>
                    </div>
                  ))}
                </div>
              </section>
              <section className="card draft-card">
                <div className="panel-heading">
                  <h2>Extracted draft</h2>
                  <span className="ai-chip">✦ AI prepared</span>
                </div>
                <p className="draft-notice">
                  Review extracted values against the original evidence before
                  approving.
                </p>
                {!data.draft ? (
                  <div className="empty-state">
                    <Icon name="clock" size={28} />
                    <h3>
                      {["QUEUED", "PROCESSING"].includes(data.status)
                        ? "Preparing your draft"
                        : "No extracted draft"}
                    </h3>
                    <p>
                      {["QUEUED", "PROCESSING"].includes(data.status)
                        ? "This page updates automatically as extraction progresses."
                        : "Extraction did not produce a draft. This request cannot be accepted until a draft is available."}
                    </p>
                  </div>
                ) : (
                  <>
                    <h3>Invoice details</h3>
                    <div className="form-grid">
                      {Object.entries(
                        data.draft.header_fields || {
                          vendor_name: null,
                          invoice_number: null,
                          invoice_date: null,
                          currency: null,
                          subtotal: null,
                          total_amount: null,
                          tax_amount: null,
                          fee_amount: null,
                          rounding_adj: null,
                        },
                      )
                        .filter(([k]) => !k.startsWith("voucher_"))
                        .map(([k, v]) => field(`header.${k}`, v, label(k)))}
                    </div>
                    <h3 className="section-title">Voucher information</h3>
                    <p className="muted voucher-help">
                      Nominal uses the reviewed total amount. Leave Nomor blank
                      to generate a voucher number; Nama defaults to the vendor,
                      and Tanggal/Periode to the invoice date.
                    </p>
                    <div className="form-grid">
                      {[
                        ["voucher_number", "Nomor"],
                        ["voucher_name", "Nama"],
                        ["voucher_purpose", "Peruntukan"],
                        ["voucher_period", "Tanggal/Periode"],
                      ].map(([key, name]) =>
                        field(
                          `header.${key}`,
                          data.draft?.header_fields?.[key] ?? null,
                          name,
                        ),
                      )}
                    </div>
                    <h3 className="section-title">Line items</h3>
                    {data.draft.line_items.map((item, i) => (
                      <div className="line-item-editor" key={item.line_item_id}>
                        <strong>Item {i + 1}</strong>
                        <div className="form-grid">
                          {[
                            "item_date",
                            "description",
                            "quantity",
                            "unit",
                            "unit_price",
                            "line_total",
                          ].map((k) =>
                            field(
                              `line_items.${i}.${k}`,
                              item[k] ?? null,
                              k === "item_date" ? "Tanggal" : label(k),
                            ),
                          )}
                        </div>
                      </div>
                    ))}
                    <h3 className="section-title">Payment details</h3>
                    {data.draft.payment_details || editable ? (
                      <div className="form-grid">
                        {Object.entries(
                          data.draft.payment_details || {
                            bank_name: null,
                            bank_account_number: null,
                            payment_reference: null,
                          },
                        ).map(([k, v]) => field(`payment.${k}`, v, label(k)))}
                      </div>
                    ) : (
                      <p className="muted">
                        Payment details were not extracted.
                      </p>
                    )}
                  </>
                )}
              </section>
            </div>
          )}
          {tab === "voucher" &&
            (["AMIGA_REVIEWED", "SENT_TO_CLIENT"].includes(data.status) ? (
              <section className="card">
                <VoucherDocument requestId={requestId!} />
              </section>
            ) : (
              <section className="card empty-state">
                <h3>Preview unavailable</h3>
                <p>Voucher preview is available after independent approval.</p>
              </section>
            ))}
          {tab === "history" && (
            <section className="card">
              <h2>Field revision history</h2>
              {!revisions.length ? (
                <div className="empty-state">No field revisions recorded.</div>
              ) : (
                revisions.map((r) => (
                  <div className="revision" key={r.revision_id}>
                    <strong>{r.field_path}</strong>
                    <span>
                      {r.old_value || "Empty"} → {r.new_value || "Empty"}
                    </span>
                    <small>{date(r.revised_at)}</small>
                  </div>
                ))
              )}
            </section>
          )}
        </>
      )}
    </>
  );
}

function Shell({ user, logout }: { user: User; logout: () => void }) {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Link to="/" className="wordmark">
          <span className="brand-icon">a</span>amiga
          <span className="brand-dot">.</span>
        </Link>
        <div className="workspace-switch">
          <span className="workspace-avatar">A</span>
          <div>
            Finance workspace<small>Invoice-to-voucher</small>
          </div>
        </div>
        <span className="nav-label">WORKSPACE</span>
        <nav>
          <NavLink to="/" end>
            <Icon name="grid" />
            Overview
          </NavLink>
          {["CLIENT_PIC", "INTAKE_SUPPORT"].includes(user.role) && (
            <NavLink to="/upload">
              <Icon name="upload" />
              New request
            </NavLink>
          )}
          <NavLink to="/vouchers">
            <Icon name="file" />
            Vouchers
          </NavLink>
          {user.role === "SYSTEM_ADMIN" && (
            <NavLink to="/templates">
              <Icon name="file" />
              Voucher templates
            </NavLink>
          )}
        </nav>
        <div className="sidebar-bottom">
          <div className="sidebar-tip">
            <span className="tip-icon">✦</span>
            <strong>A smarter way to process</strong>
            <p>
              AI-assisted extraction.
              <br />
              Human-reviewed results.
            </p>
          </div>
          <div className="user-profile">
            <span className="user-avatar">
              {user.username.slice(0, 2).toUpperCase()}
            </span>
            <div>
              <strong>{user.username}</strong>
              <small>{label(user.role)}</small>
            </div>
            <button
              className="icon-button"
              onClick={logout}
              aria-label="Sign out"
            >
              <Icon name="logout" size={17} />
            </button>
          </div>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <span>
            Invoice-to-voucher <span className="topbar-divider">/</span>{" "}
            <strong>Workspace</strong>
          </span>
          <span className="environment">
            <i className="live-dot" />
            Development workspace
          </span>
        </header>
        <main>
          <Routes>
            <Route path="/" element={<Dashboard user={user} />} />
            <Route
              path="/vouchers"
              element={<Dashboard user={user} vouchers />}
            />
            <Route
              path="/upload"
              element={
                ["CLIENT_PIC", "INTAKE_SUPPORT"].includes(user.role) ? (
                  <Upload />
                ) : (
                  <div className="card">Your role cannot submit invoices.</div>
                )
              }
            />
            <Route
              path="/requests/:requestId"
              element={<RequestView user={user} />}
            />
            <Route
              path="/templates"
              element={
                user.role === "SYSTEM_ADMIN" ? (
                  <TemplateEditor />
                ) : (
                  <div className="card">
                    Template editing is available to system administrators.
                  </div>
                )
              }
            />
            <Route
              path="*"
              element={
                <div className="empty-state">
                  <h1>Page not found</h1>
                  <Link to="/">Back to overview</Link>
                </div>
              }
            />
          </Routes>
        </main>
        <footer className="app-footer">
          <span>amiga · Thoughtful tools for finance teams</span>
          <span>Prepared with AI. Reviewed by people.</span>
        </footer>
      </div>
    </div>
  );
}
export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(!!sessionStorage.getItem(tokenKey));
  const logout = useCallback(() => {
    sessionStorage.removeItem(tokenKey);
    setUser(null);
  }, []);
  useEffect(() => {
    if (sessionStorage.getItem(tokenKey))
      api<User>("/auth/me")
        .then(setUser)
        .catch(logout)
        .finally(() => setLoading(false));
    window.addEventListener("amiga:unauthorized", logout);
    return () => window.removeEventListener("amiga:unauthorized", logout);
  }, [logout]);
  return (
    <BrowserRouter>
      {loading ? (
        <div className="login-page empty-state">Opening your workspace…</div>
      ) : user ? (
        <Shell user={user} logout={logout} />
      ) : (
        <Login onLogin={setUser} />
      )}
    </BrowserRouter>
  );
}

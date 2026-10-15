import {
  FormEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
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
  can,
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
import TaxEditor from "./components/TaxEditor";
import { TaxOption } from "./api";
import TemplateEditor from "./components/TemplateEditor";
import Organization from "./components/Organization";
import {
  ValidationWarnings,
  PasswordForm,
  HistoryTimeline,
  AllocationPanel,
  MetricsDashboard,
  ClientManager,
  PasswordInput,
} from "./components/Operations";
import Chat from "./components/Chat";
import Account from "./components/Account";
import VoucherMetadata, {
  Approver,
  ApproverFields,
} from "./components/VoucherMetadata";
import VoucherDocument from "./components/VoucherDocument";
import { PageSkeleton, Skeleton } from "./components/Skeleton";

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
    chevron: "m6 9 6 6 6-6",
    menu: "M4 6h16 M4 12h16 M4 18h16",
    close: "m6 6 12 12 M18 6 6 18",
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
const desktopLogo =
  "https://res.cloudinary.com/dyb0gqcmu/image/upload/v1792050683/Logo-AMIGA-2025.webp";
const mobileLogo =
  "https://res.cloudinary.com/dyb0gqcmu/image/upload/v1792050683/Logo-AMIGA-2025-1.webp";
function BrandLogo() {
  return (
    <picture className="brand-logo">
      <source media="(max-width: 760px)" srcSet={mobileLogo} />
      <img src={desktopLogo} alt="amiga" />
    </picture>
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
function ConfirmSignOut({
  onCancel,
  onConfirm,
}: {
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="modal-backdrop" role="presentation">
      <section
        className="confirm-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="signout-title"
      >
        <span className="confirm-modal-icon">
          <Icon name="logout" size={20} />
        </span>
        <h2 id="signout-title">Anda yakin keluar?</h2>
        <p>Anda akan keluar dari workspace saat ini.</p>
        <div className="confirm-modal-actions">
          <button type="button" className="secondary" onClick={onCancel}>
            Batal
          </button>
          <button type="button" className="danger-outline" onClick={onConfirm}>
            Sign out
          </button>
        </div>
      </section>
    </div>
  );
}
function roleTitle(role: string) {
  const titles: Record<string, string> = {
    CLIENT_PIC: "Client workspace",
    INTAKE_SUPPORT: "Intake workspace",
    FINANCE_REVIEWER: "Finance workspace",
    SERVICE_LEAD: "Service lead workspace",
    SYSTEM_ADMIN: "System admin workspace",
    USER_ADMIN: "User administration",
    SUPER_ADMIN: "Admin workspace",
  };
  return titles[role] || "Your workspace";
}

function userDisplayName(user: User) {
  return user.full_name?.trim() || user.username;
}

function userInitials(user: User) {
  return userDisplayName(user)
    .split(/\s+/)
    .map((part) => part[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
}

function Login({ onLogin }: { onLogin: (user: User) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await api<{ access_token: string }>("/auth/token", {
        method: "POST",
        body: new URLSearchParams({ username, password }),
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
          <BrandLogo />
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
        <label>
          Password
          <PasswordInput
            autoComplete="current-password"
            value={password}
            onChange={setPassword}
            required
            ariaLabel="Password"
            placeholder="********"
          />
        </label>
        <button className="primary" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
          <Icon name="arrow" />
        </button>
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
  const [clientFilter, setClientFilter] = useState("");
  const [reviewerFilter, setReviewerFilter] = useState("");
  const [dueFrom, setDueFrom] = useState("");
  const [dueTo, setDueTo] = useState("");
  const [overdueOnly, setOverdueOnly] = useState(false);
  const [timing] = useState(() => ({
    started: performance.now(),
    recorded: false,
  }));
  const load = useCallback(async () => {
    try {
      setItems(await api<Invoice[]>("/requests"));
      if (!timing.recorded) {
        timing.recorded = true;
        void api(
          "/telemetry/page-load",
          json({
            page: "dashboard",
            duration_ms: Math.round(performance.now() - timing.started),
          }),
        ).catch(() => {});
      }
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
      (!clientFilter || i.client_id === clientFilter) &&
      (!reviewerFilter || i.assigned_reviewer === reviewerFilter) &&
      (!dueFrom || (!!i.due_date && i.due_date >= dueFrom)) &&
      (!dueTo || (!!i.due_date && i.due_date <= dueTo)) &&
      (!overdueOnly || i.overdue) &&
      `${i.client_name} ${i.request_id}`
        .toLowerCase()
        .includes(search.toLowerCase()),
  );
  const canUpload = can(user, "upload");
  if (loading) {
    return (
      <>
        <div className="page-heading">
          <div>
            <span className="eyebrow">
              WORKSPACE / {vouchers ? "VOUCHERS" : "OVERVIEW"}
            </span>
            <Skeleton className="skeleton-heading" />
            <Skeleton className="skeleton-line skeleton-line-wide" />
          </div>
        </div>
        <PageSkeleton variant="dashboard" />
      </>
    );
  }
  return (
    <>
      <div className="page-heading">
        <div>
          <span className="eyebrow">
            WORKSPACE / {vouchers ? "VOUCHERS" : "OVERVIEW"}
          </span>
          <h1>
            {vouchers ? "Your vouchers" : roleTitle(user.role)}
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
              title:
                user.role === "CLIENT_PIC"
                  ? "Ready to refine"
                  : "Ready for review",
              value: items.filter(
                (i) =>
                  i.status ===
                  (user.role === "CLIENT_PIC"
                    ? "READY_FOR_REFINEMENT"
                    : "READY_FOR_REVIEW"),
              ).length,
              icon: "clock",
              note:
                user.role === "CLIENT_PIC"
                  ? "Check fields and assign taxes"
                  : "Awaiting independent review",
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
            <span>Refine</span>
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
        <div className="dashboard-filters">
          <label>
            Company
            <select
              value={clientFilter}
              onChange={(e) => setClientFilter(e.target.value)}
            >
              <option value="">All companies</option>
              {Array.from(
                new Map(items.map((i) => [i.client_id, i.client_name])),
              ).map(([id, name]) => (
                <option key={id} value={id}>
                  {name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Status
            <select value={filter} onChange={(e) => setFilter(e.target.value)}>
              <option value="all">All statuses</option>
              {Object.entries(statusLabels).map(([id, name]) => (
                <option key={id} value={id}>
                  {name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Reviewer
            <select
              value={reviewerFilter}
              onChange={(e) => setReviewerFilter(e.target.value)}
            >
              <option value="">All reviewers</option>
              {Array.from(
                new Set(items.map((i) => i.assigned_reviewer).filter(Boolean)),
              ).map((id) => (
                <option key={id} value={id!}>
                  {id === user.user_id
                    ? "Me"
                    : items.find((i) => i.assigned_reviewer === id)
                        ?.assigned_reviewer_name || id}
                </option>
              ))}
            </select>
          </label>
          <label>
            Due from
            <input
              type="date"
              value={dueFrom}
              onChange={(e) => setDueFrom(e.target.value)}
            />
          </label>
          <label>
            Due through
            <input
              type="date"
              value={dueTo}
              onChange={(e) => setDueTo(e.target.value)}
            />
          </label>
          <label>
            <input
              type="checkbox"
              checked={overdueOnly}
              onChange={(e) => setOverdueOnly(e.target.checked)}
            />
            Overdue only (WIB)
          </label>
        </div>
        <div className="table-tools">
          <div className="tabs">
            {[
              "all",
              user.role === "CLIENT_PIC"
                ? "READY_FOR_REFINEMENT"
                : "READY_FOR_REVIEW",
              "PROCESSING",
              "AMIGA_REVIEWED",
            ].map((s) => (
              <button
                key={s}
                className={filter === s ? "active" : ""}
                onClick={() => setFilter(s)}
              >
                {s === "all" ? "All requests" : statusLabels[s]}
              </button>
            ))}
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
                        {i.due_date && (
                          <small>
                            Due {i.due_date}
                            {i.overdue ? " · Overdue" : ""}
                          </small>
                        )}
                      </span>
                    </Link>
                  </td>
                  <td>{i.client_name}</td>
                  <td className="muted">{date(i.created_at)}</td>
                  <td>
                    <Badge status={i.status} />
                    {!!i.flags?.length && (
                      <small>
                        {i.flags.length} validation warning
                        {i.flags.length > 1 ? "s" : ""}
                      </small>
                    )}
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
                : search ||
                    filter !== "all" ||
                    clientFilter ||
                    reviewerFilter ||
                    dueFrom ||
                    dueTo ||
                    overdueOnly
                  ? "No matching requests"
                  : "A fresh start for your invoices"}
            </h3>
            <p>
              {loading
                ? "Connecting to your workspace."
                : search ||
                    filter !== "all" ||
                    clientFilter ||
                    reviewerFilter ||
                    dueFrom ||
                    dueTo ||
                    overdueOnly
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
        Approvals and any unresolved warnings are recorded in the audit trail.
      </div>
    </>
  );
}

function Upload() {
  const navigate = useNavigate();
  const [clients, setClients] = useState<Client[]>([]);
  const [client, setClient] = useState("");
  const [approvers, setApprovers] = useState<Approver[]>([
    { full_name: "", job_title: "" },
  ]);
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
  function handleDragOver(e: React.DragEvent<HTMLLabelElement>) {
    e.preventDefault();
    e.stopPropagation();
    setDragging(true);
  }
  function handleDragLeave(e: React.DragEvent<HTMLLabelElement>) {
    e.preventDefault();
    e.stopPropagation();
    setDragging(false);
  }
  function handleDrop(e: React.DragEvent<HTMLLabelElement>) {
    e.preventDefault();
    e.stopPropagation();
    setDragging(false);
    addFiles(Array.from(e.dataTransfer.files));
  }
  function handleFileInput(e: React.ChangeEvent<HTMLInputElement>) {
    addFiles(Array.from(e.target.files ?? []));
    e.target.value = "";
  }
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setError("");
    const form = new FormData(e.currentTarget);
    form.delete("files");
    form.set("approvers", JSON.stringify(approvers));
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
                    {c.client_code ? ` (${c.client_code})` : ""}
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
          <label>
            Peruntukan / Expense purpose
            <textarea
              name="voucher_purpose"
              required
              maxLength={1000}
              placeholder="Purpose or allocation of this expense"
            />
          </label>
          <h2 className="section-title">Disetujui Oleh</h2>
          <ApproverFields value={approvers} onChange={setApprovers} />
          <p className="muted">
            Diajukan Oleh uses your{" "}
            <Link to="/account">
              account full name, professional role, and optional signature
            </Link>
            . Voucher number and creation time are generated after approval.
          </p>
          <h2 className="section-title">Invoice & supporting evidence</h2>
          <label
            className={`dropzone${dragging ? " dragging" : ""}`}
            onDragEnter={handleDragOver}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            aria-describedby="evidence-file-help"
          >
            <span className="dropzone-icon empty-icon">
              <Icon name="upload" size={26} />
            </span>
            <strong className="dropzone-title">
              Drag files here, or <span>browse files</span>
            </strong>
            <small id="evidence-file-help" className="dropzone-help">
              PDF, PNG or JPEG · Up to 20 MB per file · Maximum 20 files
            </small>
            <input
              type="file"
              accept="application/pdf,image/png,image/jpeg"
              multiple
              aria-label="Choose invoice evidence"
              onChange={handleFileInput}
            />
          </label>
          {files.length > 0 && (
          <div className="file-list" aria-label="Selected evidence files">
            {files.map((f, i) => (
              <div className="file-list-item" key={`${f.name}-${i}`}>
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
          )}
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
              "Refine and submit",
              "Check extracted fields, assign item taxes, and submit to Finance Reviewer for acceptance or rejection.",
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
  const [metadataDirty, setMetadataDirty] = useState(false);
  const [taxDirty, setTaxDirty] = useState<Record<string, boolean>>({});
  const [taxOptions, setTaxOptions] = useState<TaxOption[]>([]);
  const handleTaxDirty = useCallback(
    (id: string, dirty: boolean) =>
      setTaxDirty((prev) =>
        prev[id] === dirty ? prev : { ...prev, [id]: dirty },
      ),
    [],
  );
  useEffect(() => {
    api<TaxOption[]>("/tax-options")
      .then(setTaxOptions)
      .catch((e) => setError(e.message));
  }, []);
  const [reason, setReason] = useState("");
  const [rejecting, setRejecting] = useState(false);
  const [evidenceIndex, setEvidenceIndex] = useState(0);
  const [evidenceUrl, setEvidenceUrl] = useState("");
  const [evidenceError, setEvidenceError] = useState("");
  const [evidenceRetry, setEvidenceRetry] = useState(0);
  const [tab, setTab] = useState("draft");

  const [timing] = useState(() => ({
    started: performance.now(),
    recorded: false,
  }));
  const load = useCallback(async () => {
    try {
      setData(await api<Detail>(`/requests/${requestId}`));
      if (!timing.recorded) {
        timing.recorded = true;
        void api(
          "/telemetry/page-load",
          json({
            page: "review",
            duration_ms: Math.round(performance.now() - timing.started),
          }),
        ).catch(() => {});
      }
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
    if (
      !metadataDirty &&
      !Object.keys(changes).length &&
      !Object.values(taxDirty).some(Boolean)
    )
      return;
    const guard = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [changes, taxDirty, metadataDirty]);
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
  const refining =
    can(user, "refine") && data?.status === "READY_FOR_REFINEMENT";
  const editable =
    !!data &&
    (refining || (can(user, "review") && data.status === "READY_FOR_REVIEW"));

  const dirty =
    metadataDirty ||
    Object.keys(changes).length > 0 ||
    Object.values(taxDirty).some(Boolean);
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
            {refining ? "Refine invoice" : "Invoice review"}
            <span className="heading-dot">.</span>
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
          {data.draft?.source_invoices &&
            data.draft.source_invoices.length > 1 && (
              <section className="card">
                <h2>Combined source invoices</h2>
                <p>
                  All invoice items, printed taxes and additional charges are
                  included below. Printed taxes are already included; only
                  assign an additional tax if intended.
                </p>
                {data.draft.source_invoices.map((invoice, index) => (
                  <p key={index}>
                    {invoice.header?.invoice_number || `Invoice ${index + 1}`} ·{" "}
                    {invoice.header?.invoice_date || "Date unavailable"} ·{" "}
                    {invoice.header?.currency} {invoice.header?.total_amount}
                  </p>
                ))}
              </section>
            )}
          {can(user, "upload") &&
            (data.status === "EXCEPTION" ||
              (data.status === "READY_FOR_REFINEMENT" &&
                !data.draft?.header_fields &&
                !data.draft?.line_items.length)) && (
              <section className="card">
                <p>
                  Extraction did not produce usable values. Retry using the
                  original uploaded documents.
                </p>
                <button
                  className="primary"
                  disabled={busy}
                  onClick={() => action("retry-extraction")}
                >
                  {busy ? "Retrying…" : "Retry extraction"}
                </button>
              </section>
            )}
          {!!data.validation_issues?.length && (
            <ValidationWarnings issues={data.validation_issues} />
          )}
          {can(user, "allocate") && (
            <AllocationPanel
              requestId={requestId!}
              data={data}
              onSaved={load}
            />
          )}
          <div className="detail-toolbar">
            <div className="tabs">
              {[
                "draft",
                ...(can(user, "voucher") ? ["voucher"] : []),
                ...(can(user, "history") ? ["history"] : []),
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
              {refining && (
                <button
                  className="primary"
                  disabled={busy || dirty || !data.draft}
                  onClick={() => action("submit-review")}
                >
                  {busy ? "Submitting…" : "Submit to Finance Reviewer"}
                </button>
              )}
              {editable && (
                <button
                  className="secondary"
                  disabled={!Object.keys(changes).length || busy}
                  onClick={save}
                >
                  {busy
                    ? "Saving…"
                    : `Save changes${dirty ? ` (${Object.keys(changes).length})` : ""}`}
                </button>
              )}
              {can(user, "approve") && data.status === "READY_FOR_REVIEW" && (
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
                    {busy ? "Generating voucher…" : "Accept & generate voucher"}
                  </button>
                </>
              )}
              {can(user, "voucher") &&
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
                      Nominal comes from the extracted total amount. The bottom
                      TOTAL includes assigned taxes. Number and creation time
                      are generated once on approval.
                    </p>
                    <div className="form-grid">
                      <label>
                        Nomor
                        <input
                          disabled
                          value={
                            data.voucher_number || "Generated after approval"
                          }
                        />
                      </label>
                      <label>
                        Tanggal/Periode
                        <input
                          disabled
                          value={
                            data.voucher_created_at
                              ? new Date(
                                  data.voucher_created_at,
                                ).toLocaleString("id-ID", {
                                  timeZone: "Asia/Jakarta",
                                }) + " WIB"
                              : "Set when the voucher is generated"
                          }
                        />
                      </label>
                      {field(
                        "header.voucher_name",
                        data.draft?.header_fields?.voucher_name ??
                          data.draft?.header_fields?.vendor_name ??
                          null,
                        "Nama",
                      )}
                    </div>
                    <h3>Diajukan Oleh</h3>
                    <div className="form-grid">
                      <label>
                        Submitter full name
                        <input
                          disabled
                          value={
                            data.submitter?.full_name ||
                            "Complete the uploader’s account"
                          }
                        />
                      </label>
                      <label>
                        Submitter professional role
                        <input
                          disabled
                          value={
                            data.submitter?.job_title ||
                            "Complete the uploader’s account"
                          }
                        />
                      </label>
                    </div>
                    <VoucherMetadata
                      key={`${requestId}-${data.voucher_purpose}-${JSON.stringify(data.approvers)}`}
                      requestId={requestId!}
                      purpose={data.voucher_purpose || ""}
                      approvers={data.approvers || []}
                      editable={
                        can(user, "refine") &&
                        data.status === "READY_FOR_REFINEMENT"
                      }
                      onDirty={setMetadataDirty}
                      onSaved={() => {
                        void load();
                      }}
                    />
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
                        <TaxEditor
                          requestId={requestId!}
                          itemId={item.line_item_id}
                          lineTotal={item.line_total ?? null}
                          assigned={data.tax_assignments?.[item.line_item_id]}
                          options={taxOptions}
                          editable={editable && !busy}
                          onDirty={handleTaxDirty}
                          onSaved={load}
                          onSaving={setBusy}
                        />
                      </div>
                    ))}
                    {data.totals && (
                      <div className="voucher-totals">
                        <h3>Voucher calculation</h3>
                        {[
                          ["Item amounts", data.totals.base_amount],
                          ["Additional tax", data.totals.added_tax],
                          ["Withholding", data.totals.withheld_tax],
                          ["Fees", data.totals.fees],
                          ["Rounding", data.totals.rounding],
                          ["Voucher total", data.totals.voucher_total],
                        ].map(([name, value]) => (
                          <div key={name!}>
                            <span>{name}</span>
                            <strong>
                              {value === null
                                ? "Enter all item totals"
                                : new Intl.NumberFormat("id-ID", {
                                    maximumFractionDigits: 2,
                                  }).format(Number(value))}
                            </strong>
                          </div>
                        ))}
                        {data.totals.calculated_from_items && (
                          <p>
                            Calculated from item amounts and selected tax rows.
                            Extracted invoice tax/total are shown above for
                            reference and are not added again.
                          </p>
                        )}
                      </div>
                    )}
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
          {tab === "history" && <HistoryTimeline requestId={requestId!} />}
        </>
      )}
    </>
  );
}

function Shell({ user, logout }: { user: User; logout: () => void }) {
  const [accountMenuOpen, setAccountMenuOpen] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [signOutOpen, setSignOutOpen] = useState(false);
  const accountMenuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!accountMenuOpen) return;
    const closeMenu = (event: MouseEvent) => {
      if (
        accountMenuRef.current &&
        !accountMenuRef.current.contains(event.target as Node)
      ) {
        setAccountMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", closeMenu);
    return () => document.removeEventListener("mousedown", closeMenu);
  }, [accountMenuOpen]);

  return (
    <div className={`app-shell${sidebarCollapsed ? " sidebar-collapsed" : ""}`}>
      <aside className="sidebar">
        <Link to="/" className="wordmark">
          <BrandLogo />
        </Link>
        <button
          type="button"
          className="sidebar-toggle"
          aria-label={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
          aria-expanded={!sidebarCollapsed}
          onClick={() => setSidebarCollapsed((collapsed) => !collapsed)}
        >
          <Icon name="chevron" size={18} />
        </button>
        <button
          type="button"
          className="mobile-menu-button"
          aria-label={mobileMenuOpen ? "Close navigation" : "Open navigation"}
          aria-expanded={mobileMenuOpen}
          onClick={() => setMobileMenuOpen((open) => !open)}
        >
          <Icon name={mobileMenuOpen ? "close" : "menu"} size={21} />
        </button>
        <div className="workspace-switch">
          <span className="workspace-avatar">{userInitials(user)}</span>
          <div>
            {userDisplayName(user)}
            <small>{roleTitle(user.role)}</small>
          </div>
        </div>
        <span className="nav-label">WORKSPACE</span>
        <nav
          className={mobileMenuOpen ? "mobile-menu-open" : ""}
          onClick={() => setMobileMenuOpen(false)}
        >
          <NavLink to="/" end>
            <Icon name="grid" />
            Overview
          </NavLink>
          {can(user, "upload") && (
            <NavLink to="/upload">
              <Icon name="upload" />
              New request
            </NavLink>
          )}
          {can(user, "voucher") && (
            <NavLink to="/vouchers">
              <Icon name="file" />
              Vouchers
            </NavLink>
          )}
          {can(user, "templates") && (
            <NavLink to="/templates">
              <Icon name="file" />
              Voucher templates
            </NavLink>
          )}
          <NavLink to="/account">
            <Icon name="file" />
            Your account
          </NavLink>

          {(can(user, "users") || can(user, "assignments")) && (
            <NavLink to="/users">
              <Icon name="grid" />
              People & assignments
            </NavLink>
          )}
          {can(user, "metrics") && (
            <NavLink to="/metrics">
              <Icon name="clock" />
              Performance
            </NavLink>
          )}
          {can(user, "clients") && (
            <NavLink to="/clients">
              <Icon name="grid" />
              Companies
            </NavLink>
          )}
          {can(user, "chat") && (
            <NavLink to="/chat">
              <Icon name="search" />
              Invoice chat
            </NavLink>
          )}
        </nav>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <span className="topbar-breadcrumb">
            Invoice-to-voucher <span className="topbar-divider">/</span>{" "}
            <strong>Workspace</strong>
          </span>
          <div className="topbar-actions">
            <div className="account-menu" ref={accountMenuRef}>
              <button
                className="account-menu-trigger"
                onClick={() => setAccountMenuOpen((open) => !open)}
                aria-expanded={accountMenuOpen}
                aria-haspopup="menu"
              >
                <span className="user-avatar">
                  {user.username.slice(0, 2).toUpperCase()}
                </span>
                <span className="account-menu-name">{user.username}</span>
                <Icon name="chevron" size={15} />
              </button>
              {accountMenuOpen && (
                <div className="account-menu-dropdown" role="menu">
                  <Link
                    to="/account"
                    role="menuitem"
                    onClick={() => setAccountMenuOpen(false)}
                  >
                    Your account
                  </Link>
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      setAccountMenuOpen(false);
                      setSignOutOpen(true);
                    }}
                    aria-label="Sign out"
                  >
                    <Icon name="logout" size={16} />
                    Sign out
                  </button>
                </div>
              )}
            </div>
          </div>
        </header>
        <main>
          <Routes>
            <Route path="/chat" element={<Chat />} />
            <Route
              path="/account"
              element={
                <div className="account-page">
                  <Account key="profile" />
                  <PasswordForm onChanged={logout} />
                </div>
              }
            />
            <Route
              path="/users"
              element={
                can(user, "users") || can(user, "assignments") ? (
                  <Organization user={user} />
                ) : (
                  <div className="card">
                    Your role cannot manage users or assignments.
                  </div>
                )
              }
            />
            <Route
              path="/metrics"
              element={
                can(user, "metrics") ? (
                  <MetricsDashboard />
                ) : (
                  <div className="card">
                    Your role cannot view performance metrics.
                  </div>
                )
              }
            />
            <Route
              path="/clients"
              element={
                can(user, "clients") ? (
                  <ClientManager />
                ) : (
                  <div className="card">Your role cannot manage companies.</div>
                )
              }
            />
            <Route
              path="/"
              element={
                user.role === "USER_ADMIN" ? (
                  <Organization user={user} />
                ) : (
                  <Dashboard user={user} />
                )
              }
            />
            <Route
              path="/vouchers"
              element={<Dashboard user={user} vouchers />}
            />
            <Route
              path="/upload"
              element={
                can(user, "upload") ? (
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
                can(user, "templates") ? (
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
      {signOutOpen && (
        <ConfirmSignOut
          onCancel={() => setSignOutOpen(false)}
          onConfirm={logout}
        />
      )}
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
      ) : user?.must_change_password ? (
        <div className="login-page">
          <PasswordForm forced onChanged={logout} />
          <button onClick={logout}>Sign out</button>
        </div>
      ) : user ? (
        <Shell user={user} logout={logout} />
      ) : (
        <Login onLogin={setUser} />
      )}
    </BrowserRouter>
  );
}

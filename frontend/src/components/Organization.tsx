import { ChangeEvent, FormEvent, useEffect, useState } from "react";
import { api, apiFetch, can, Client, json, User } from "../api";
import { PasswordInput } from "./Operations";
import { PageSkeleton } from "./Skeleton";

const operational = [
  "CLIENT_PIC",
  "INTAKE_SUPPORT",
  "FINANCE_REVIEWER",
  "SERVICE_LEAD",
];

type Person = User & { assignments: string[]; is_active: boolean };

export default function Organization({ user }: { user: User }) {
  const [people, setPeople] = useState<Person[]>([]);
  const [clients, setClients] = useState<Client[]>([]);
  const [search, setSearch] = useState("");
  const [currentPage, setCurrentPage] = useState(1);
  const [itemsPerPage, setItemsPerPage] = useState(10);
  const [hasNextPage, setHasNextPage] = useState(false);
  const [selected, setSelected] = useState<Person | null>(null);
  const [username, setUsername] = useState("");
  const [name, setName] = useState("");
  const [title, setTitle] = useState("");
  const [role, setRole] = useState("CLIENT_PIC");
  const [password, setPassword] = useState("");
  const [client, setClient] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [importing, setImporting] = useState(false);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [isModalOpen, setIsModalOpen] = useState(false);

  const roles =
    user.role === "SUPER_ADMIN"
      ? [...operational, "SYSTEM_ADMIN", "SUPER_ADMIN"]
      : operational;
  const manage = can(user, "users");

  async function load() {
    const rows = await api<Person[]>(
      `/users?search=${encodeURIComponent(search)}&offset=${
        (currentPage - 1) * itemsPerPage
      }&limit=${itemsPerPage + 1}`,
    );
    setHasNextPage(rows.length > itemsPerPage);
    setPeople(rows.slice(0, itemsPerPage));
  }

  useEffect(() => {
    setLoading(true);
    load()
      .catch((e) => setMessage(e.message))
      .finally(() => setLoading(false));
  }, [search, currentPage, itemsPerPage]);

  useEffect(() => {
    if (!can(user, "assignments")) return;
    api<Client[]>(["SUPER_ADMIN", "SYSTEM_ADMIN"].includes(user.role) ? "/clients/all" : "/clients")
      .then(setClients)
      .catch((e) => setMessage(e.message));
  }, [user.role]);

  /** Open "Manage account" inline — sets selected + expanded state for that user */
  function expand(p: Person) {
    const isAlreadyExpanded = expandedId === p.user_id;
    if (isAlreadyExpanded) {
      setExpandedId(null);
      setSelected(null);
    } else {
      setExpandedId(p.user_id);
      setSelected(p);
      setRole(p.role || "CLIENT_PIC");
      setPassword("");
      setMessage("");
    }
  }

  /** Reset "Create account" form fields */
  function openCreateModal() {
    setUsername("");
    setName("");
    setTitle("");
    setRole("CLIENT_PIC");
    setPassword("");
    setClient("");
    setMessage("");
    setIsModalOpen(true);
  }

  async function importCsv(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    e.target.value = "";
    setImporting(true);
    setMessage(`Importing ${file.name}...`);
    try {
      const body = new FormData();
      body.append("file", file);
      const response = await apiFetch("/api/users/import-csv", {
        method: "POST",
        body,
      });
      const result = (await response.json()) as { imported: number };
      await load();
      setMessage(`${result.imported} account(s) imported successfully.`);
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setImporting(false);
    }
  }

  async function run(action: () => Promise<unknown>, success: string) {
    setBusy(true);
    setMessage("");
    try {
      await action();
      await load();
      setPassword("");
      setMessage(success);
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function create(e: FormEvent) {
    e.preventDefault();
    void run(
      () =>
        api(
          "/users",
          json({
            username,
            full_name: name,
            job_title: title,
            role,
            temporary_password: password,
            assignments:
              can(user, "assignments") && client ? [{ client_id: client }] : [],
          }),
        ),
      "Account created. Share the temporary password privately; a change is required at first login.",
    );
    setIsModalOpen(false);
  }

  async function assignment(id: string, remove: boolean) {
    await run(
      () =>
        apiFetch(
          `/api/users/${selected!.user_id}/assignments${remove ? `/${id}` : ""}`,
          remove ? { method: "DELETE" } : json({ client_id: id }),
        ),
      "Assignment updated.",
    );
    const updated = await api<Person[]>(
      `/users?search=${encodeURIComponent(selected!.username)}`,
    );
    const refreshed = updated.find((p) => p.user_id === selected!.user_id) || null;
    setSelected(refreshed);
  }

  return (
    <section className="organization-page">
      {/* ── Header ─────────────────────────────────────────────────── */}
      <div className="organization-heading">
        <div>
          <p className="organization-eyebrow">Organization</p>
          <h1>People & Assignments</h1>
          <p className="organization-subtitle">
            Manage account access, roles, and company assignments.
          </p>
        </div>
        {manage && (
          <div className="organization-actions">
            <label className={`button button-secondary csv-upload${importing ? " is-loading" : ""}`}>
              <input
                type="file"
                accept=".csv,text/csv"
                onChange={importCsv}
                disabled={importing}
              />
              {importing ? "Importing..." : "Import CSV"}
            </label>
            <button
              className="button button-primary"
              type="button"
              onClick={openCreateModal}
            >
              + Account
            </button>
          </div>
        )}
      </div>

      {/* ── Toolbar ────────────────────────────────────────────────── */}
      <div className="organization-toolbar">
        <label className="directory-search">
          <span className="search-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="18" height="18">
              <circle cx="11" cy="11" r="6.5" />
              <path d="m16 16 4.5 4.5" />
            </svg>
          </span>
          <span className="sr-only">Search accounts</span>
          <input
            value={search}
            placeholder="search.."
            onChange={(e) => {
              setSearch(e.target.value);
              setCurrentPage(1);
            }}
          />
        </label>
        <div className="directory-toolbar-summary">
          <span className="directory-count">
            {people.length} accounts on this page
          </span>
          <label className="directory-page-size">
            Show list
            <select
              value={itemsPerPage}
              onChange={(e) => {
                setItemsPerPage(Number(e.target.value));
                setCurrentPage(1);
              }}
            >
              <option value={10}>10</option>
              <option value={25}>25</option>
              <option value={50}>50</option>
            </select>
          </label>
        </div>
      </div>

      <p role="status">{message}</p>

      {/* ── Directory (full-width) ──────────────────────────────────── */}
      {loading ? (
        <PageSkeleton />
      ) : (
        <section className="card directory-card">
          <div className="directory-card-heading">
            <div>
              <p className="card-kicker">Team members</p>
              <h2>Directory</h2>
            </div>
          </div>

          <div className="directory-list">
            {people.map((p) => {
              const isExpanded = expandedId === p.user_id;
              const isSelected = selected?.user_id === p.user_id;
              return (
                <div
                  className={`directory-item${isExpanded ? " is-expanded" : ""}`}
                  key={p.user_id}
                >
                  {/* Accordion trigger */}
                  <button
                    className="directory-person"
                    type="button"
                    aria-expanded={isExpanded}
                    onClick={() => expand(p)}
                  >
                    <span className="directory-avatar">
                      {(p.full_name || p.username).slice(0, 1).toUpperCase()}
                    </span>
                    <span className="directory-person-copy">
                      <strong>{p.full_name || p.username}</strong>
                      <span>{p.job_title || p.username}</span>
                    </span>
                    <span className="directory-meta">
                      <span className="role-badge">{p.role}</span>
                      <span
                        className={`status-badge ${
                          p.is_active ? "is-active" : "is-inactive"
                        }`}
                      >
                        {p.is_active ? "Active" : "Inactive"}
                      </span>
                    </span>
                    <span className="accordion-chevron" aria-hidden="true">
                      {isExpanded ? "−" : "+"}
                    </span>
                  </button>

                  {/* Accordion body — inline manage panel */}
                  <div className="directory-expand">
                    <div className="directory-expand-inner">
                      <div className="inline-manage-topbar">
                        <p className="directory-expand-username">
                          @{p.username}
                        </p>
                      </div>

                      {manage && isSelected && (
                        <>
                          <div className="inline-manage-section">
                            <p className="inline-manage-section-title">Role</p>
                            <div className="inline-manage-field-row">
                              <label className="inline-manage-field">
                                <span className="im-label-text">System role</span>
                                <select
                                  value={role}
                                  onChange={(e) => setRole(e.target.value)}
                                >
                                  {roles.map((r) => (
                                    <option key={r}>{r}</option>
                                  ))}
                                </select>
                              </label>
                              <button
                                className="button button-secondary"
                                disabled={busy}
                                onClick={() =>
                                  run(
                                    () =>
                                      api(
                                        `/users/${p.user_id}/role`,
                                        json({ role }, "PATCH"),
                                      ),
                                    "Role updated.",
                                  )
                                }
                              >
                                Save role
                              </button>
                            </div>
                          </div>

                          <div className="inline-manage-section">
                            <p className="inline-manage-section-title">
                              Reset password
                            </p>
                            <div className="inline-manage-field-row">
                              <label className="inline-manage-field">
                                <span className="im-label-text">
                                  Temporary password
                                </span>
                                <span className="im-password-wrap">
                                  <PasswordInput
                                    autoComplete="new-password"
                                    value={password}
                                    minLength={12}
                                    maxLength={128}
                                    onChange={setPassword}
                                  />
                                </span>
                              </label>
                              <button
                                className="button button-secondary"
                                disabled={busy || password.length < 12}
                                onClick={() =>
                                  run(
                                    () =>
                                      api(
                                        `/users/${p.user_id}/reset-password`,
                                        json({ temporary_password: password }),
                                      ),
                                    "Password reset; user must change it on next login.",
                                  )
                                }
                              >
                                Reset password
                              </button>
                            </div>
                          </div>
                        </>
                      )}

                      {can(user, "assignments") && isSelected && (
                        <div className="inline-manage-section">
                          <p className="inline-manage-section-title">
                            Company assignments
                          </p>

                          {selected!.assignments.length > 0 && (
                            <div className="inline-manage-assignment-list">
                              {selected!.assignments.map((id) => (
                                <div
                                  key={id}
                                  className="inline-manage-revoke-row"
                                >
                                  <span>
                                    {clients.find((c) => c.client_id === id)
                                      ?.client_name || id}
                                  </span>
                                  <button
                                    className="button button-secondary"
                                    disabled={busy}
                                    onClick={() => assignment(id, true)}
                                  >
                                    Revoke
                                  </button>
                                </div>
                              ))}
                            </div>
                          )}

                          <div className="inline-manage-field-row">
                            <label className="inline-manage-field">
                              <span className="im-label-text">Add company</span>
                              <select
                                value={client}
                                onChange={(e) => setClient(e.target.value)}
                              >
                                <option value="">Choose company</option>
                                {clients.map((c) => (
                                  <option key={c.client_id} value={c.client_id}>
                                    {c.client_name}
                                  </option>
                                ))}
                              </select>
                            </label>
                            <button
                              className="button button-primary"
                              disabled={busy || !client}
                              onClick={() => assignment(client, false)}
                            >
                              Assign
                            </button>
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
            {!people.length && (
              <p className="directory-empty">No accounts match your search.</p>
            )}
          </div>

          <div className="directory-pagination">
            <button
              className="button button-secondary"
              type="button"
              disabled={currentPage === 1}
              onClick={() => setCurrentPage((page) => Math.max(1, page - 1))}
            >
              Previous
            </button>
            <span className="directory-page-number" aria-current="page">
              {currentPage}
            </span>
            <button
              className="button button-secondary"
              type="button"
              disabled={!hasNextPage}
              onClick={() => setCurrentPage((page) => page + 1)}
            >
              Next
            </button>
          </div>
        </section>
      )}

      {/* ── Create Account Modal ────────────────────────────────────── */}
      {isModalOpen && (
        <div
          className="modal-overlay"
          role="dialog"
          aria-modal="true"
          aria-labelledby="create-account-title"
          onClick={(e) => {
            if (e.target === e.currentTarget) setIsModalOpen(false);
          }}
        >
          <div className="modal-panel">
            <div className="modal-header">
              <h2 id="create-account-title">Create account</h2>
              <button
                className="modal-close"
                type="button"
                aria-label="Close"
                onClick={() => setIsModalOpen(false)}
              >
                ✕
              </button>
            </div>

            <form className="management-form" onSubmit={create}>
              <label>
                Username
                <input
                  required
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                />
              </label>
              <label>
                Full name
                <input
                  required
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </label>
              <label>
                Professional role
                <input
                  required
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                />
              </label>
              <label>
                System role
                <select
                  value={role}
                  onChange={(e) => setRole(e.target.value)}
                >
                  {roles.map((r) => (
                    <option key={r}>{r}</option>
                  ))}
                </select>
              </label>
              <label>
                Temporary password
                <PasswordInput
                  autoComplete="new-password"
                  required
                  minLength={12}
                  maxLength={128}
                  value={password}
                  onChange={setPassword}
                />
              </label>
              {can(user, "assignments") && (
                <label>
                  Initial company (optional)
                  <select
                    value={client}
                    onChange={(e) => setClient(e.target.value)}
                  >
                    <option value="">No assignment</option>
                    {clients.map((c) => (
                      <option key={c.client_id} value={c.client_id}>
                        {c.client_name}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <div className="modal-footer">
                <button
                  type="button"
                  className="button button-secondary"
                  onClick={() => setIsModalOpen(false)}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="button button-primary"
                  disabled={busy}
                >
                  Create account
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </section>
  );
}

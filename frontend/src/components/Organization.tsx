import { FormEvent, useEffect, useState } from "react";
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
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Person | null>(null);
  const [username, setUsername] = useState("");
  const [name, setName] = useState("");
  const [title, setTitle] = useState("");
  const [role, setRole] = useState("CLIENT_PIC");
  const [password, setPassword] = useState("");
  const [client, setClient] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const roles =
    user.role === "SUPER_ADMIN"
      ? [...operational, "SYSTEM_ADMIN", "USER_ADMIN", "SUPER_ADMIN"]
      : operational;
  const manage = can(user, "users");
  async function load() {
    setPeople(
      await api<Person[]>(
        `/users?search=${encodeURIComponent(search)}&offset=${offset}`,
      ),
    );
  }
  useEffect(() => {
    setLoading(true);
    load()
      .catch((e) => setMessage(e.message))
      .finally(() => setLoading(false));
  }, [search, offset]);
  useEffect(() => {
    if (!can(user, "assignments")) return;
    api<Client[]>(user.role === "SUPER_ADMIN" ? "/clients/all" : "/clients")
      .then(setClients)
      .catch((e) => setMessage(e.message));
  }, [user.role]);

  function choose(p: Person | null) {
    setSelected(p);
    setUsername(p?.username || "");
    setName(p?.full_name || "");
    setTitle(p?.job_title || "");
    setRole(p?.role || "CLIENT_PIC");
    setPassword("");
    setMessage("");
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
    setSelected(updated.find((p) => p.user_id === selected!.user_id) || null);
  }
  return (
    <section>
      <h1>People & assignments</h1>
      <p>Access is controlled by role and active company assignments.</p>
      <label>
        Search accounts
        <input
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setOffset(0);
          }}
        />
      </label>
      <p role="status">{message}</p>
      {loading ? (
        <PageSkeleton />
      ) : (
      <div className="organization-grid">
        <section className="card">
          <h2>Directory</h2>
          {manage && (
            <button onClick={() => choose(null)}>Create account</button>
          )}
          {people.map((p) => (
            <button
              className="directory-person"
              key={p.user_id}
              onClick={() => choose(p)}
            >
              {p.full_name || p.username} · {p.role} ·{" "}
              {p.is_active ? "Active" : "Inactive"}
            </button>
          ))}
          <button
            disabled={!offset}
            onClick={() => setOffset(Math.max(0, offset - 50))}
          >
            Previous
          </button>
          <button
            disabled={people.length < 50}
            onClick={() => setOffset(offset + 50)}
          >
            Next
          </button>
        </section>
        <section className="card">
          {selected ? (
            <>
              <h2>{selected.username}</h2>
              {manage && (
                <>
                  <label>
                    Role
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
                    disabled={busy}
                    onClick={() =>
                      run(
                        () =>
                          api(
                            `/users/${selected.user_id}/role`,
                            json({ role }, "PATCH"),
                          ),
                        "Role updated.",
                      )
                    }
                  >
                    Save role
                  </button>
                  <button
                    disabled={busy}
                    onClick={() =>
                      run(async () => {
                        await api(
                          `/users/${selected.user_id}/status`,
                          json({ is_active: !selected.is_active }, "PATCH"),
                        );
                        setSelected({
                          ...selected,
                          is_active: !selected.is_active,
                        });
                      }, "Account status updated.")
                    }
                  >
                    {selected.is_active ? "Deactivate" : "Activate"}
                  </button>
                  <label>
                    Temporary password
                    <PasswordInput
                      autoComplete="new-password"
                      value={password}
                      minLength={12}
                      maxLength={128}
                      onChange={setPassword}
                    />
                  </label>
                  <button
                    disabled={busy || password.length < 12}
                    onClick={() =>
                      run(
                        () =>
                          api(
                            `/users/${selected.user_id}/reset-password`,
                            json({ temporary_password: password }),
                          ),
                        "Password reset; user must change it on next login.",
                      )
                    }
                  >
                    Reset password
                  </button>
                </>
              )}
              {can(user, "assignments") && (
                <>
                  <h3>Company assignments</h3>
                  {selected.assignments.map((id) => (
                    <p key={id}>
                      {clients.find((c) => c.client_id === id)?.client_name ||
                        id}{" "}
                      <button
                        disabled={busy}
                        onClick={() => assignment(id, true)}
                      >
                        Revoke
                      </button>
                    </p>
                  ))}
                  <label>
                    Company
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
                    disabled={busy || !client}
                    onClick={() => assignment(client, false)}
                  >
                    Assign company
                  </button>
                </>
              )}
            </>
          ) : manage ? (
            <form className="management-form" onSubmit={create}>
              <h2>Create account</h2>
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
                <select value={role} onChange={(e) => setRole(e.target.value)}>
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
              <button disabled={busy}>Create account</button>
            </form>
          ) : (
            <p>Select an account to manage its assignments.</p>
          )}
        </section>
      </div>
      )}
    </section>
  );
}

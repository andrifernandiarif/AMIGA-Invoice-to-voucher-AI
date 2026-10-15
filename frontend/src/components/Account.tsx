import { FormEvent, useEffect, useState } from "react";
import { api, Client, json, User } from "../api";
async function readSignature(file: File): Promise<string> {
  if (
    !["image/png", "image/jpeg"].includes(file.type) ||
    file.size > 2 * 1024 * 1024
  )
    throw new Error("Choose a PNG or JPEG signature under 2 MB.");
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error("Could not read signature."));
    reader.readAsDataURL(file);
  });
}
export default function Account({ create = false }: { create?: boolean }) {
  const [name, setName] = useState("");
  const [title, setTitle] = useState("");
  const [signature, setSignature] = useState<string | null>(null);
  const [remove, setRemove] = useState(false);
  const [clients, setClients] = useState<Client[]>([]);
  const [assigned, setAssigned] = useState<string[]>([]);
  const [role, setRole] = useState("CLIENT_PIC");
  const [username, setUsername] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    setReady(false);
    setError("");
    setMessage("");
    setName("");
    setTitle("");
    setSignature(null);
    (create
      ? api<Client[]>("/clients/all").then(setClients)
      : api<{
          full_name: string;
          job_title: string;
          signature_data: string | null;
        }>("/users/me/profile").then((p) => {
          setName(p.full_name);
          setTitle(p.job_title);
          setSignature(p.signature_data);
        })
    )
      .then(() => setReady(true))
      .catch((e) => setError(e.message));
  }, [create]);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMessage("");
    setError("");
    try {
      const profile = {
        full_name: name,
        job_title: title,
        signature_data: signature,
        remove_signature: remove,
      };
      await api<User>(
        create ? "/users" : "/users/me/profile",
        json(
          create
            ? {
                ...profile,
                username,
                role,
                assignments: assigned.map((client_id) => ({ client_id })),
              }
            : profile,
          create ? "POST" : "PUT",
        ),
      );
      setMessage(
        create
          ? `Account created for ${name}.`
          : "Account saved. These details will be used on future vouchers.",
      );
      if (create) {
        setName("");
        setTitle("");
        setUsername("");
        setSignature(null);
        setAssigned([]);
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <div className="page-heading">
        <div>
          <span className="eyebrow">WORKSPACE / ACCOUNT</span>
          <h1>{create ? "Create a user" : "Your account"}</h1>
          <p>
            Set the full name, professional role, and optional signature used in
            Diajukan Oleh.
          </p>
        </div>
      </div>
      <form className="card account-form" onSubmit={submit}>
        {error && <p role="alert">{error}</p>}
        {message && <p role="status">{message}</p>}
        <div className="form-grid">
          {create && (
            <label>
              Username
              <input
                required
                maxLength={200}
                value={username}
                onChange={(e) => setUsername(e.target.value)}
              />
            </label>
          )}
          <label>
            Full name
            <input
              required
              maxLength={300}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          <label>
            Professional role / Jabatan
            <input
              required
              maxLength={200}
              placeholder="e.g. Admin, Operations Manager"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
          </label>
          {create && (
            <label>
              System access role
              <select value={role} onChange={(e) => setRole(e.target.value)}>
                {[
                  "CLIENT_PIC",
                  "INTAKE_SUPPORT",
                  "FINANCE_REVIEWER",
                  "SERVICE_LEAD",
                  "SYSTEM_ADMIN",
                ].map((r) => (
                  <option key={r}>{r}</option>
                ))}
              </select>
            </label>
          )}
        </div>
        {create && (
          <fieldset>
            <legend>Client assignments (choose at least one)</legend>
            {clients.map((c) => (
              <label className="assignment-option" key={c.client_id}>
                <input
                  type="checkbox"
                  checked={assigned.includes(c.client_id)}
                  onChange={(e) =>
                    setAssigned(
                      e.target.checked
                        ? [...assigned, c.client_id]
                        : assigned.filter((id) => id !== c.client_id),
                    )
                  }
                />
                {c.client_name} ({c.client_code})
              </label>
            ))}
          </fieldset>
        )}
        <h3>Signature (optional)</h3>
        <p className="muted">
          Upload your own signature as PNG or JPEG, under 2 MB. It will appear
          on vouchers you submit. Without an upload, the signature space stays
          empty.
        </p>
        <input
          aria-label="Upload signature"
          key={`${create}-${username === ""}`}
          type="file"
          accept="image/png,image/jpeg"
          disabled={busy || !ready}
          onChange={async (e) => {
            const file = e.target.files?.[0];
            if (file)
              try {
                setSignature(await readSignature(file));
                setRemove(false);
                setError("");
              } catch (err) {
                setError((err as Error).message);
                e.target.value = "";
              }
          }}
        />
        {signature && (
          <div>
            <img
              className="signature-preview"
              src={signature}
              alt="Your uploaded signature"
            />
            <button
              type="button"
              className="button secondary"
              onClick={() => {
                setSignature(null);
                setRemove(true);
              }}
            >
              Remove signature
            </button>
          </div>
        )}
        <p>
          <button
            className="button primary"
            disabled={
              busy ||
              !ready ||
              !name.trim() ||
              !title.trim() ||
              (create && (!username.trim() || !assigned.length))
            }
          >
            {busy ? "Saving…" : create ? "Create account" : "Save account"}
          </button>
        </p>
      </form>
    </>
  );
}

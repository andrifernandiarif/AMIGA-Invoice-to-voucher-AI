  import { FormEvent, useEffect, useRef, useState } from "react";
  import { api, Client, json, User } from "../api";
  import { PageSkeleton } from "./Skeleton";

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
    const [signatureFileName, setSignatureFileName] = useState("");
    const [remove, setRemove] = useState(false);
    const [clients, setClients] = useState<Client[]>([]);
    const [assigned, setAssigned] = useState<string[]>([]);
    const [role, setRole] = useState("CLIENT_PIC");
    const [username, setUsername] = useState("");
    const [error, setError] = useState("");
    const [message, setMessage] = useState("");
    const [busy, setBusy] = useState(false);
    const [ready, setReady] = useState(false);
    const signatureInput = useRef<HTMLInputElement>(null);

    useEffect(() => {
      setReady(false);
      setError("");
      setMessage("");
      setName("");
      setTitle("");
      setSignature(null);
      setSignatureFileName("");
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
            setSignatureFileName("");
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
          setSignatureFileName("");
          setAssigned([]);
        }
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setBusy(false);
      }
    }

    const canSubmit =
      !busy &&
      ready &&
      name.trim() &&
      title.trim() &&
      (!create || (username.trim() && assigned.length));

    return (
      <>
        <div className="page-heading">
          <div>
            <span className="eyebrow">WORKSPACE / ACCOUNT</span>
            <h1>{create ? "Create a user" : "Your account"}</h1>
            <p>
              Set the full name, professional role, and optional signature used
              in Diajukan Oleh.
            </p>
          </div>
        </div>

        {!ready && error && (
          <div className="error-banner" role="alert">
            {error}
          </div>
        )}
        {ready ? (
        <form className="card account-form" onSubmit={submit}>
          {error && (
            <div className="error-banner" role="alert">
              {error}
            </div>
          )}
          {message && (
            <div className="success-banner" role="status">
              {message}
            </div>
          )}

          {/* ==== SECTION: Account details ==== */}
          <section className="form-section">
            <h3 className="section-title">Account details</h3>
            <p className="muted">
              These values appear on vouchers as Diajukan Oleh.
            </p>

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
          </section>

          {/* ==== SECTION: Client assignments ==== */}
          {create && (
            <section className="form-section">
              <h3 className="section-title">Client assignments</h3>
              <p className="muted">Choose at least one client.</p>

              <div className="assignment-list">
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
                    <span>
                      {c.client_name}
                      {c.client_code ? ` (${c.client_code})` : ""}
                    </span>
                  </label>
                ))}
              </div>
            </section>
          )}

          {/* ==== SECTION: Signature ==== */}
          <section
            className="form-section signature-section"
            aria-labelledby="signature-title"
          >
            <div className="signature-heading">
              <div>
                <h3 id="signature-title" className="section-title">
                  Signature (optional)
                </h3>
                <p className="muted">
                  Upload a PNG or JPEG under 2 MB. It will appear on vouchers you
                  submit.
                </p>
              </div>
              {signature && (
                <span className="signature-status">
                  {signatureFileName ? "New signature" : "Signature saved"}
                </span>
              )}
            </div>

            <div className="signature-upload">
              <input
                ref={signatureInput}
                aria-label="Upload signature"
                className="signature-file-input"
                type="file"
                accept="image/png,image/jpeg"
                disabled={busy || !ready}
                onChange={async (e) => {
                  const file = e.target.files?.[0];
                  if (!file) return;
                  try {
                    setSignature(await readSignature(file));
                    setSignatureFileName(file.name);
                    setRemove(false);
                    setError("");
                  } catch (err) {
                    setError((err as Error).message);
                    e.target.value = "";
                  }
                }}
              />
              <button
                type="button"
                className="secondary signature-upload-button"
                onClick={() => signatureInput.current?.click()}
                disabled={busy || !ready}
              >
                Choose file
              </button>
              <span className="signature-file-name">
                {signatureFileName || "No file selected"}
              </span>
              <small>PNG or JPEG · maximum 2 MB</small>
            </div>

            {signature && (
              <div className="signature-preview-card">
                <img
                  className="signature-preview"
                  src={signature}
                  alt="Your uploaded signature"
                />
                <div className="signature-preview-info">
                  <strong>{signatureFileName || "Current signature"}</strong>
                  <small>
                    {signatureFileName
                      ? "Ready to save with your account"
                      : "Currently used on your vouchers"}
                  </small>
                  <div className="signature-actions">
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => signatureInput.current?.click()}
                      disabled={busy || !ready}
                    >
                      Replace
                    </button>
                    <button
                      type="button"
                      className="text-button"
                      aria-label="Remove signature"
                      onClick={() => {
                        setSignature(null);
                        setSignatureFileName("");
                        setRemove(true);
                        if (signatureInput.current)
                          signatureInput.current.value = "";
                      }}
                      disabled={busy || !ready}
                    >
                      Remove
                    </button>
                  </div>
                </div>
              </div>
            )}
          </section>

          {/* ==== FOOTER: actions ==== */}
          <div className="form-footer">
            <button className="primary" disabled={!canSubmit}>
              {busy ? "Saving…" : create ? "Create account" : "Save account"}
            </button>
          </div>
        </form>
        ) : (
          <PageSkeleton variant="form" />
        )}
      </>
    );
  }
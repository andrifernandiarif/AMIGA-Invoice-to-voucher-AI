import { FormEvent, useCallback, useEffect, useState } from "react";
import { api } from "../api";
interface Template {
  template_id: string;
  version: string;
  template_content: string;
  is_active: boolean;
}
interface Templates {
  templates: Template[];
  default_template: string;
}
export default function TemplateEditor() {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [content, setContent] = useState("");
  const [version, setVersion] = useState("");
  const [source, setSource] = useState("default");
  const [defaultContent, setDefaultContent] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [dirty, setDirty] = useState(false);
  const load = useCallback(async () => {
    const data = await api<Templates>("/admin/templates");
    setTemplates(data.templates);
    setDefaultContent(data.default_template);
    const active = data.templates.find((t) => t.is_active);
    setContent(active?.template_content ?? data.default_template);
    setSource(active?.template_id ?? "default");
    setDirty(false);
  }, []);
  useEffect(() => {
    load()
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [load]);
  useEffect(() => {
    if (!dirty) return;
    const unload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    const click = (e: MouseEvent) => {
      const link = (e.target as Element).closest("a");
      if (
        link?.getAttribute("href")?.startsWith("/") &&
        !window.confirm("Discard unsaved template changes?")
      ) {
        e.preventDefault();
        e.stopPropagation();
      }
    };
    window.addEventListener("beforeunload", unload);
    document.addEventListener("click", click, true);
    return () => {
      window.removeEventListener("beforeunload", unload);
      document.removeEventListener("click", click, true);
    };
  }, [dirty]);
  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setMessage("");
    const body = new FormData();
    body.append("version", version);
    body.append("template_content", content);
    try {
      await api("/admin/templates", { method: "POST", body });
      await load();
      setVersion("");
      setMessage(
        "Template saved and activated. Future accepted requests will use this version.",
      );
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
          <span className="eyebrow">WORKSPACE / VOUCHER TEMPLATES</span>
          <h1>
            Voucher templates<span className="heading-dot">.</span>
          </h1>
          <p>
            Edit the layout used to generate vouchers when a reviewer accepts a
            request.
          </p>
        </div>
      </div>
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
      {loading ? (
        <div className="card empty-state">Loading templates…</div>
      ) : (
        <form className="card template-editor" onSubmit={save}>
          <div className="form-grid">
            <label>
              Start from
              <select
                value={source}
                disabled={busy}
                onChange={(e) => {
                  if (
                    dirty &&
                    !window.confirm("Discard unsaved template changes?")
                  )
                    return;
                  setSource(e.target.value);
                  setContent(
                    templates.find((t) => t.template_id === e.target.value)
                      ?.template_content ?? defaultContent,
                  );
                  setDirty(false);
                  setMessage("");
                }}
              >
                <option value="default">Bundled default</option>
                {templates.map((t) => (
                  <option key={t.template_id} value={t.template_id}>
                    {t.version}
                    {t.is_active ? " · Active" : ""}
                  </option>
                ))}
              </select>
            </label>
            <label>
              New version name
              <input
                required
                maxLength={100}
                value={version}
                disabled={busy}
                placeholder="e.g. v1.1"
                onChange={(e) => {
                  setVersion(e.target.value);
                  setDirty(true);
                }}
              />
            </label>
          </div>
          <div className="template-help">
            <p>
              Save edits as a new version. The new version becomes active for
              future approvals. Existing vouchers keep their original PDF.
            </p>
            <p>
              Required fields: <code>{"{{ vendor_name }}"}</code>,{" "}
              <code>{"{{ invoice_number }}"}</code>,{" "}
              <code>line_items_table</code>, and{" "}
              <code>{"{{ authorization_disclaimer }}"}</code>. The bundled
              template includes examples for line items, totals and payment
              details.
            </p>
          </div>
          <label>
            Import an HTML template
            <input
              type="file"
              accept=".html,.htm,text/html"
              disabled={busy}
              onChange={async (e) => {
                const file = e.target.files?.[0];
                if (!file) return;
                if (file.size > 250000) {
                  setError("Choose a template under 250 KB.");
                  return;
                }
                try {
                  setContent(await file.text());
                  setDirty(true);
                  setMessage("");
                } catch {
                  setError("Unable to read that template file.");
                }
              }}
            />
          </label>
          <label className="section-title">
            Template HTML
            <textarea
              aria-label="Template HTML"
              className="template-code"
              required
              spellCheck={false}
              maxLength={250000}
              disabled={busy}
              value={content}
              onChange={(e) => {
                setContent(e.target.value);
                setDirty(true);
                setMessage("");
              }}
            />
          </label>
          <div className="form-footer">
            <button
              className="primary"
              disabled={busy || !version.trim() || !content.trim()}
            >
              {busy ? "Saving…" : "Save & activate template"}
            </button>
          </div>
        </form>
      )}
    </>
  );
}

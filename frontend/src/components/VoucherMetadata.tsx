import { useState } from "react";
import { api, json } from "../api";
export interface Approver {
  full_name: string;
  job_title: string;
}
export function ApproverFields({
  value,
  onChange,
  disabled = false,
}: {
  value: Approver[];
  onChange: (value: Approver[]) => void;
  disabled?: boolean;
}) {
  return (
    <>
      <p className="muted">
        Enter the client signatories and their professional roles. Their
        signature spaces stay empty for signing.
      </p>
      {value.map((person, i) => (
        <div className="line-item-editor" key={i}>
          <strong>Client approver {i + 1}</strong>
          <div className="form-grid">
            <label>
              Full name
              <input
                required
                maxLength={300}
                disabled={disabled}
                value={person.full_name}
                onChange={(e) =>
                  onChange(
                    value.map((p, n) =>
                      n === i ? { ...p, full_name: e.target.value } : p,
                    ),
                  )
                }
              />
            </label>
            <label>
              Professional role / Jabatan
              <input
                required
                maxLength={200}
                disabled={disabled}
                placeholder="e.g. BOD, Manager of PT X"
                value={person.job_title}
                onChange={(e) =>
                  onChange(
                    value.map((p, n) =>
                      n === i ? { ...p, job_title: e.target.value } : p,
                    ),
                  )
                }
              />
            </label>
          </div>
          {!disabled && value.length > 1 && (
            <button
              type="button"
              className="button secondary"
              onClick={() => onChange(value.filter((_, n) => n !== i))}
            >
              Remove approver
            </button>
          )}
        </div>
      ))}
      {!disabled && value.length < 12 && (
        <button
          className="button secondary"
          type="button"
          onClick={() => onChange([...value, { full_name: "", job_title: "" }])}
        >
          + Add another approver
        </button>
      )}
    </>
  );
}
export default function VoucherMetadata({
  requestId,
  purpose,
  approvers,
  editable,
  onSaved,
  onDirty,
}: {
  requestId: string;
  purpose: string;
  approvers: Approver[];
  editable: boolean;
  onSaved: () => void;
  onDirty: (dirty: boolean) => void;
}) {
  const [value, setValue] = useState(purpose);
  const [people, setPeople] = useState<Approver[]>(
    approvers.length ? approvers : [{ full_name: "", job_title: "" }],
  );
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  async function save() {
    setBusy(true);
    setError("");
    setSaved(false);
    try {
      await api(
        `/requests/${requestId}/voucher-metadata`,
        json({ voucher_purpose: value, approvers: people }, "PUT"),
      );
      setSaved(true);
      onDirty(false);
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="voucher-metadata">
      <label>
        Peruntukan / Expense purpose
        <textarea
          required
          maxLength={1000}
          disabled={!editable}
          value={value}
          onChange={(e) => {
            setValue(e.target.value);
            setSaved(false);
            onDirty(true);
          }}
        />
      </label>
      <h3>Disetujui Oleh</h3>
      <ApproverFields
        value={people}
        disabled={!editable}
        onChange={(v) => {
          setPeople(v);
          setSaved(false);
          onDirty(true);
        }}
      />
      {error && <p role="alert">{error}</p>}
      {editable && (
        <p>
          <button
            type="button"
            className="button secondary"
            disabled={
              busy ||
              !value.trim() ||
              people.some((p) => !p.full_name.trim() || !p.job_title.trim())
            }
            onClick={save}
          >
            {busy ? "Saving…" : "Save upload details"}
          </button>
          {saved && <span role="status"> Saved</span>}
        </p>
      )}
    </div>
  );
}

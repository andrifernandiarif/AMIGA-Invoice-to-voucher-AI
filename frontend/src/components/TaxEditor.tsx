import { useEffect, useState } from "react";
import { api, json, TaxAssignment, TaxOption } from "../api";
interface Props {
  requestId: string;
  itemId: string;
  lineTotal: string | number | null;
  assigned?: TaxAssignment;
  options: TaxOption[];
  editable: boolean;
  onDirty: (id: string, dirty: boolean) => void;
  onSaved: () => Promise<void>;
  onSaving: (busy: boolean) => void;
}
const initial = (tax?: TaxAssignment) => ({
  profile: tax?.profile || (tax ? `${tax.tax_type}_MANUAL` : "NO_TAX"),
  rate: tax?.rate_percent || "",
  manualBase: tax?.base_is_manual || false,
  base: tax?.base_amount || "",
  effect: tax?.effect || "ADD",
});
export default function TaxEditor({
  requestId,
  itemId,
  lineTotal,
  assigned,
  options,
  editable,
  onDirty,
  onSaved,
  onSaving,
}: Props) {
  const [form, setForm] = useState(initial(assigned));
  const [saved, setSaved] = useState(initial(assigned));
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const serverKey = JSON.stringify(assigned || null);
  useEffect(() => {
    const value = initial(assigned);
    setForm(value);
    setSaved(value);
  }, [serverKey]);
  const option = options.find((o) => o.id === form.profile);
  const rate = option?.rate_percent ?? form.rate;
  const base = form.manualBase ? form.base : lineTotal;
  const normalize = (value: typeof form) =>
    JSON.stringify({
      profile: value.profile,
      effect: value.effect,
      base: value.manualBase ? value.base : null,
      rate:
        options.find((o) => o.id === value.profile)?.rate_percent ?? value.rate,
    });
  const dirty = normalize(form) !== normalize(saved);
  useEffect(() => {
    onDirty(itemId, dirty);
  }, [dirty, itemId, onDirty]);
  const calculated =
    form.profile !== "NO_TAX" && base !== null && base !== "" && rate !== ""
      ? (Number(base) * Number(rate)) / 100
      : 0;
  async function apply() {
    setSaving(true);
    onSaving(true);
    setError("");
    try {
      const result = await api<{ tax_assignment: TaxAssignment | null }>(
        `/requests/${requestId}/items/${itemId}/tax`,
        json(
          {
            profile: form.profile,
            rate_percent: form.profile === "NO_TAX" ? null : rate || null,
            base_amount:
              form.manualBase && form.profile !== "NO_TAX"
                ? form.base || null
                : null,
            effect: form.effect,
          },
          "PUT",
        ),
      );
      const value = initial(result.tax_assignment ?? undefined);
      setForm(value);
      setSaved(value);
      await onSaved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
      onSaving(false);
    }
  }
  return (
    <div className="tax-editor">
      <label>
        Tax assignment
        <select
          aria-label={`Tax assignment for item ${itemId}`}
          disabled={!editable || saving}
          value={form.profile}
          onChange={(e) => {
            const next = options.find((o) => o.id === e.target.value);
            setForm((prev) => ({
              ...prev,
              profile: e.target.value,
              rate: next?.rate_percent ?? "",
            }));
            setError("");
          }}
        >
          <option value="NO_TAX">Tanpa pajak</option>
          {options
            .filter((o) => o.id !== "NO_TAX")
            .map((o) => (
              <option key={o.id} value={o.id}>
                {o.label}
              </option>
            ))}
        </select>
      </label>
      {form.profile !== "NO_TAX" && (
        <>
          <div className="form-grid">
            <label>
              Rate (%)
              <input
                aria-label={`Tax rate for item ${itemId}`}
                type="number"
                min="0"
                max="100"
                step="0.0001"
                disabled={!editable || saving || option?.rate_percent != null}
                value={rate}
                onChange={(e) =>
                  setForm((prev) => ({ ...prev, rate: e.target.value }))
                }
                placeholder="Enter applicable rate"
              />
            </label>
            <label>
              Voucher treatment
              <select
                disabled={!editable || saving}
                value={form.effect}
                onChange={(e) =>
                  setForm((prev) => ({
                    ...prev,
                    effect: e.target.value as TaxAssignment["effect"],
                  }))
                }
              >
                <option value="ADD">Tambahkan ke voucher</option>
                <option value="DEDUCT">Potong dari pembayaran</option>
              </select>
            </label>
          </div>
          <label className="tax-checkbox">
            <input
              type="checkbox"
              checked={form.manualBase}
              disabled={!editable || saving}
              onChange={(e) =>
                setForm((prev) => ({
                  ...prev,
                  manualBase: e.target.checked,
                  base: prev.base || String(lineTotal ?? ""),
                }))
              }
            />
            Enter tax base manually
          </label>
          <label>
            Tax base
            <input
              aria-label={`Tax base for item ${itemId}`}
              type="number"
              min="0"
              step="0.0001"
              value={base ?? ""}
              disabled={!editable || saving || !form.manualBase}
              onChange={(e) =>
                setForm((prev) => ({ ...prev, base: e.target.value }))
              }
            />
          </label>
          <div className="tax-preview">
            <span>
              {form.effect === "DEDUCT" ? "Deduction" : "Additional tax row"}
            </span>
            <strong>
              {new Intl.NumberFormat("id-ID", {
                maximumFractionDigits: 2,
              }).format(Number.isFinite(calculated) ? calculated : 0)}
            </strong>
          </div>
          {option && (
            <p className="tax-note">
              {option.note}{" "}
              {option.source_url && (
                <a href={option.source_url} target="_blank" rel="noreferrer">
                  Ketentuan DJP ↗
                </a>
              )}
            </p>
          )}
          {option?.tax_type.startsWith("PPH") && (
            <p className="tax-note">
              PPh umumnya dipotong dari pembayaran. Pilih Tambahkan untuk biaya
              pajak atau Potong untuk pemotongan.
            </p>
          )}
        </>
      )}
      {error && (
        <p className="tax-error" role="alert">
          {error}
        </p>
      )}
      {editable && dirty && (
        <button
          className="secondary"
          disabled={
            saving ||
            (form.profile !== "NO_TAX" &&
              (!option || rate === "" || base === null || base === ""))
          }
          onClick={apply}
        >
          {saving ? "Applying…" : "Apply tax"}
        </button>
      )}
    </div>
  );
}

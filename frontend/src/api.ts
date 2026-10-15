export const tokenKey = "amiga_access_token";
export async function apiFetch(
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = sessionStorage.getItem(tokenKey);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(path, { ...init, headers });
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      message =
        typeof body.detail === "string"
          ? body.detail
          : JSON.stringify(body.detail ?? body);
    } catch {
      /* use HTTP message */
    }
    if (response.status === 401) {
      sessionStorage.removeItem(tokenKey);
      window.dispatchEvent(new Event("amiga:unauthorized"));
    }
    throw new Error(message);
  }
  return response;
}
export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  return (await apiFetch(`/api${path}`, init)).json();
}
export const json = (body: unknown, method = "POST"): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});
export interface User {
  user_id: string;
  username: string;
  full_name?: string | null;
  job_title?: string | null;
  role: string;
}
export interface Client {
  client_id: string;
  client_name: string;
  client_code?: string | null;
}
export interface Invoice {
  request_id: string;
  client_id: string;
  client_name: string;
  status: string;
  flags: string[];
  created_at: string;
  request_date: string;
  channel: string;
}
export interface TaxOption {
  id: string;
  tax_type: string;
  label: string;
  rate_percent: string | null;
  note: string;
  source_url?: string;
}
export interface TaxAssignment {
  tax_type: string;
  profile: string | null;
  rate_percent: string;
  base_amount: string | null;
  base_is_manual: boolean;
  effect: "ADD" | "DEDUCT";
  amount: string;
}
export interface VoucherTotals {
  base_amount: string | null;
  added_tax: string;
  withheld_tax: string;
  fees: string;
  rounding: string;
  voucher_total: string | null;
  calculated_from_items: boolean;
}
export interface Detail {
  voucher_purpose?: string | null;
  approvers?: { full_name: string; job_title: string }[];
  voucher_number?: string | null;
  voucher_created_at?: string | null;
  submitter?: { full_name?: string; job_title?: string };
  request_id: string;
  client_id: string;
  status: string;
  flags: string[];
  rejection_reason: string | null;
  tax_assignments?: Record<string, TaxAssignment>;
  totals?: VoucherTotals | null;
  refinement_submitted_at?: string | null;
  evidence_files: {
    file_key: string;
    original_filename: string;
    content_type: string;
  }[];
  draft: {
    header_fields: Record<string, string | null> | null;
    line_items: (Record<string, string | number | null> & {
      line_item_id: string;
      sequence_number: number;
    })[];
    payment_details: Record<string, string | null> | null;
  } | null;
}
export const statusLabels: Record<string, string> = {
  QUEUED: "Queued",
  PROCESSING: "Processing",
  READY_FOR_REFINEMENT: "Ready to refine",
  READY_FOR_REVIEW: "Submitted for review",
  EXCEPTION: "Extraction issue",
  AMIGA_REVIEWED: "Approved",
  SENT_TO_CLIENT: "Sent to client",
  REJECTED: "Rejected",
};

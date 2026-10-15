import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
const user = {
  user_id: "u",
  username: "finance",
  role: "FINANCE_REVIEWER",
  permissions: ["review", "approve", "voucher", "history", "chat"],
};
const response = (body: unknown) => ({ ok: true, json: async () => body });
beforeEach(() => {
  sessionStorage.clear();
  window.history.replaceState({}, "", "/");
  vi.stubGlobal("fetch", vi.fn());
});
afterEach(() => {
  vi.unstubAllGlobals();
});
describe("Workspace", () => {
  it("signs in and sends the bearer token to workspace APIs", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(
        response({ access_token: "test-token" }) as Response,
      )
      .mockResolvedValueOnce(response(user) as Response)
      .mockResolvedValue(response([]) as Response);
    render(<App />);
    await userEvent.type(screen.getByLabelText("Username"), "finance");
    await userEvent.type(
      screen.getByLabelText("Password"),
      "Finance-Password-2026",
    );
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByRole("heading", { name: "Finance workspace." });
    expect(sessionStorage.getItem("amiga_access_token")).toBe("test-token");
    const call = vi
      .mocked(fetch)
      .mock.calls.find(([url]) => url === "/api/requests");
    expect(new Headers(call?.[1]?.headers).get("Authorization")).toBe(
      "Bearer test-token",
    );
    expect(
      screen.queryByRole("link", { name: "New request" }),
    ).not.toBeInTheDocument();
  });
  it("filters real request data and signs out", async () => {
    sessionStorage.setItem("amiga_access_token", "token");
    vi.mocked(fetch).mockImplementation(
      async (url) =>
        response(
          url === "/api/auth/me"
            ? user
            : [
                {
                  request_id: "12345678-1234-1234-1234-123456789abc",
                  client_name: "Acme",
                  status: "READY_FOR_REVIEW",
                  flags: [],
                  created_at: "2026-10-14T00:00:00Z",
                  channel: "upload",
                },
              ],
        ) as Response,
    );
    render(<App />);
    await screen.findByText("Acme", { selector: "td" });
    await userEvent.type(screen.getByLabelText("Search requests"), "missing");
    expect(screen.getByText("No matching requests")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /finance/i }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Sign out" }));
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() =>
      expect(
        screen.getByRole("heading", { name: "Sign in to your workspace" }),
      ).toBeInTheDocument(),
    );
    expect(sessionStorage.getItem("amiga_access_token")).toBeNull();
  });
});

it("lets a reviewer accept a draft without flag gates and download evidence separately", async () => {
  const requestId = "12345678-1234-1234-1234-123456789abc";
  window.history.replaceState({}, "", `/requests/${requestId}`);
  sessionStorage.setItem("amiga_access_token", "token");
  const draft = {
    request_id: requestId,
    client_id: "c",
    status: "READY_FOR_REVIEW",
    flags: ["MISSING_EVIDENCE"],
    rejection_reason: null,
    evidence_files: [
      {
        file_key: "evidence/invoice.pdf",
        original_filename: "Invoice.pdf",
        content_type: "application/pdf",
      },
    ],
    draft: {
      header_fields: { vendor_name: "Vendor", total_amount: "99" },
      line_items: [],
      payment_details: null,
    },
  };
  vi.stubGlobal(
    "URL",
    class extends URL {
      static createObjectURL = vi.fn(() => "blob:test");
      static revokeObjectURL = vi.fn();
    },
  );
  const click = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(() => {});
  vi.mocked(fetch).mockImplementation(async (url, init) => {
    if (url === "/api/auth/me") return response(user) as Response;
    if (String(url).includes("/evidence/"))
      return {
        ok: true,
        blob: async () => new Blob(["%PDF-evidence"]),
      } as Response;
    if (String(url).endsWith("/approve") && init?.method === "POST") {
      draft.status = "AMIGA_REVIEWED";
      return response({
        status: "approved",
        voucher: { voucher_pdf_id: "v" },
      }) as Response;
    }
    return response(draft) as Response;
  });
  render(<App />);
  const accept = await screen.findByRole("button", {
    name: "Accept & generate voucher",
  });
  expect(accept).toBeEnabled();
  expect(
    screen.queryByText("This request needs attention"),
  ).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Download" }));
  await waitFor(() =>
    expect(
      vi
        .mocked(fetch)
        .mock.calls.some(([url]) => String(url).includes("?download=true")),
    ).toBe(true),
  );
  await userEvent.click(accept);
  await screen.findByRole("button", { name: "Download voucher" });
  expect(
    vi
      .mocked(fetch)
      .mock.calls.some(
        ([url, init]) =>
          String(url).endsWith("/voucher-pdf") && init?.method === "POST",
      ),
  ).toBe(false);
  click.mockRestore();
});

it("lets Client PIC refine a draft and locks editing after submitting for finance review", async () => {
  const rid = "12345678-1234-1234-1234-123456789abc";
  window.history.replaceState({}, "", `/requests/${rid}`);
  sessionStorage.setItem("amiga_access_token", "token");
  const detail = {
    request_id: rid,
    client_id: "c",
    status: "READY_FOR_REFINEMENT",
    flags: [],
    rejection_reason: null,
    evidence_files: [],
    tax_assignments: {},
    draft: {
      header_fields: { vendor_name: "Vendor" },
      line_items: [
        {
          line_item_id: "item",
          sequence_number: 1,
          description: "Service",
          quantity: "1",
          unit_price: "100",
          line_total: "100",
        },
      ],
      payment_details: null,
    },
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url, init?: RequestInit) => {
      if (url === "/api/auth/me")
        return response({
          user_id: "pic",
          username: "client",
          role: "CLIENT_PIC",
          permissions: [
            "upload",
            "refine",
            "submit",
            "voucher",
            "history",
            "chat",
          ],
        });
      if (url === "/api/tax-options") return response([]);
      if (String(url).endsWith("/submit-review") && init?.method === "POST") {
        detail.status = "READY_FOR_REVIEW";
        return response({ status: detail.status });
      }
      return response(detail);
    }),
  );
  render(<App />);
  await screen.findByRole("heading", { name: "Refine invoice." });
  expect(screen.getByLabelText("Vendor name")).toBeEnabled();
  expect(screen.getByLabelText("Tax assignment for item item")).toBeEnabled();
  await userEvent.click(
    screen.getByRole("button", { name: "Submit to Finance Reviewer" }),
  );
  await waitFor(() =>
    expect(screen.getByLabelText("Vendor name")).toBeDisabled(),
  );
  expect(
    screen.queryByRole("button", { name: "Submit to Finance Reviewer" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Accept & generate voucher" }),
  ).not.toBeInTheDocument();
});

it("uploads purpose and multiple named client approvers with evidence", async () => {
  window.history.replaceState({}, "", "/upload");
  sessionStorage.setItem("amiga_access_token", "token");
  const pic = {
    user_id: "u",
    username: "pic",
    role: "CLIENT_PIC",
    permissions: ["upload", "refine", "submit", "voucher", "history", "chat"],
    full_name: "Rina",
    job_title: "Admin",
  };
  vi.mocked(fetch).mockImplementation(
    async (url, init) =>
      response(
        url === "/api/auth/me"
          ? pic
          : url === "/api/clients"
            ? [{ client_id: "c", client_name: "Acme", client_code: "ACME" }]
            : url === "/api/requests" && init?.method === "POST"
              ? { request_id: "new-request" }
              : url === "/api/requests/new-request"
                ? {
                    request_id: "new-request",
                    status: "QUEUED",
                    evidence_files: [],
                    draft: null,
                    flags: [],
                  }
                : [],
      ) as Response,
  );
  render(<App />);
  await screen.findByRole("option", { name: "Acme (ACME)" });
  await userEvent.selectOptions(screen.getByLabelText("Client"), "c");
  await userEvent.type(
    screen.getByLabelText("Peruntukan / Expense purpose"),
    "Office supplies",
  );
  await userEvent.type(screen.getAllByLabelText("Full name")[0], "Budi");
  await userEvent.type(
    screen.getAllByLabelText("Professional role / Jabatan")[0],
    "BOD",
  );
  await userEvent.click(
    screen.getByRole("button", { name: "+ Add another approver" }),
  );
  await userEvent.type(screen.getAllByLabelText("Full name")[1], "Sari");
  await userEvent.type(
    screen.getAllByLabelText("Professional role / Jabatan")[1],
    "Manager",
  );
  await userEvent.upload(
    screen.getByLabelText("Choose invoice evidence"),
    new File(["%PDF"], "invoice.pdf", { type: "application/pdf" }),
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Submit for extraction" }),
  );
  await waitFor(() =>
    expect(
      vi
        .mocked(fetch)
        .mock.calls.some(
          ([url, init]) => url === "/api/requests" && init?.method === "POST",
        ),
    ).toBe(true),
  );
  const call = vi
    .mocked(fetch)
    .mock.calls.find(
      ([url, init]) => url === "/api/requests" && init?.method === "POST",
    );
  const form = call![1]!.body as FormData;
  expect(form.get("voucher_purpose")).toBe("Office supplies");
  expect(JSON.parse(form.get("approvers") as string)).toEqual([
    { full_name: "Budi", job_title: "BOD" },
    { full_name: "Sari", job_title: "Manager" },
  ]);
  expect(form.getAll("files")).toHaveLength(1);
});

it("lets Client PIC retry a blank extraction using the original evidence", async () => {
  const rid = "12345678-1234-1234-1234-123456789abc";
  window.history.replaceState({}, "", `/requests/${rid}`);
  sessionStorage.setItem("amiga_access_token", "token");
  const detail = {
    request_id: rid,
    client_id: "c",
    status: "READY_FOR_REFINEMENT",
    flags: [],
    rejection_reason: null,
    evidence_files: [],
    draft: { header_fields: null, line_items: [], payment_details: null },
  };
  vi.mocked(fetch).mockImplementation(async (url, init) => {
    if (url === "/api/auth/me")
      return response({
        user_id: "pic",
        username: "pic",
        role: "CLIENT_PIC",
        permissions: ["upload", "refine", "submit", "voucher", "history"],
      }) as Response;
    if (String(url).endsWith("/tax-options")) return response([]) as Response;
    if (String(url).endsWith("/retry-extraction") && init?.method === "POST") {
      detail.status = "QUEUED";
      return response({ status: "QUEUED" }) as Response;
    }
    return response(detail) as Response;
  });
  render(<App />);
  await userEvent.click(
    await screen.findByRole("button", { name: "Retry extraction" }),
  );
  await waitFor(() =>
    expect(
      screen.queryByRole("button", { name: "Retry extraction" }),
    ).not.toBeInTheDocument(),
  );
  expect(
    vi
      .mocked(fetch)
      .mock.calls.some(
        ([url, init]) =>
          String(url).endsWith("/retry-extraction") && init?.method === "POST",
      ),
  ).toBe(true);
});

it("hides Amiga AI without chat permission", async () => {
  sessionStorage.setItem("amiga_access_token", "token");
  vi.mocked(fetch).mockImplementation(
    async (url) =>
      response(
        url === "/api/auth/me" ? { ...user, permissions: [] } : [],
      ) as Response,
  );
  render(<App />);
  await screen.findByRole("heading", { name: "Finance workspace." });
  expect(
    screen.queryByRole("button", { name: "Open Amiga AI" }),
  ).not.toBeInTheDocument();
});

it("clears Amiga AI drafts when the user signs out and signs in again", async () => {
  sessionStorage.setItem("amiga_access_token", "token");
  vi.mocked(fetch).mockImplementation(
    async (url) =>
      response(
        url === "/api/auth/me"
          ? user
          : url === "/api/auth/login"
            ? { access_token: "new-token" }
            : [],
      ) as Response,
  );
  render(<App />);
  await userEvent.click(
    await screen.findByRole("button", { name: "Open Amiga AI" }),
  );
  await userEvent.type(
    screen.getByLabelText("Your question"),
    "Private session draft",
  );
  await userEvent.keyboard("{Escape}");
  await userEvent.click(screen.getByRole("button", { name: /finance/i }));
  await userEvent.click(screen.getByRole("menuitem", { name: "Sign out" }));
  await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
  await screen.findByRole("heading", { name: "Sign in to your workspace" });
  await userEvent.type(screen.getByLabelText("Username"), "finance");
  await userEvent.type(
    screen.getByLabelText("Password"),
    "Finance-Password-2026",
  );
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
  await userEvent.click(
    await screen.findByRole("button", { name: "Open Amiga AI" }),
  );
  expect(screen.getByLabelText("Your question")).toHaveValue("");
});

it.each([
  ["PROCESSING", "PENDING", "Retrying extraction automatically (attempt 2 of 3). Your original uploaded documents are saved."],
  ["EXCEPTION", "FAILED", "Extraction could not finish after 3 attempts. Your original uploaded documents are saved. Retry extraction to try again."],
])("shows extraction progress for %s without calling every failure unusable values", async (status, jobStatus, message) => {
  const rid = "12345678-1234-1234-1234-123456789abc";
  window.history.replaceState({}, "", `/requests/${rid}`);
  sessionStorage.setItem("amiga_access_token", "token");
  vi.mocked(fetch).mockImplementation(async url => response(
    url === "/api/auth/me" ? { user_id: "pic", username: "pic", role: "CLIENT_PIC", permissions: ["upload", "refine", "track"] }
    : String(url).endsWith("/tax-options") ? [] : {
      request_id: rid, client_id: "c", status, flags: [], rejection_reason: null, evidence_files: [], draft: null,
      extraction: { status: jobStatus, attempt: jobStatus === "FAILED" ? 3 : 2, max_attempts: 3, message },
    }
  ) as Response);
  render(<App />);
  await screen.findByText(message);
  expect(screen.queryByText(/Extraction did not produce usable values/)).not.toBeInTheDocument();
  if (status === "PROCESSING") expect(screen.queryByRole("button", { name: "Retry extraction" })).not.toBeInTheDocument();
  else expect(screen.getByRole("button", { name: "Retry extraction" })).toBeInTheDocument();
});

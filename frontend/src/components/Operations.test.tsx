import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { HistoryTimeline, PasswordForm, AllocationPanel, ValidationWarnings } from "./Operations";
import Organization from "./Organization";
import { Detail } from "../api";
const response = (data: unknown) =>
  ({ ok: true, json: async () => data }) as Response;
beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});
afterEach(() => {
  vi.unstubAllGlobals();
});
it("requires a temporary password change and signs out after success", async () => {
  vi.mocked(fetch).mockResolvedValue(response({ message: "Changed" }));
  const changed = vi.fn();
  render(<PasswordForm forced onChanged={changed} />);
  expect(
    screen.getByRole("heading", { name: "Change your temporary password" }),
  ).toBeInTheDocument();
  await userEvent.type(
    screen.getByLabelText("Current password"),
    "Temporary-Pass-2026",
  );
  await userEvent.type(
    screen.getByLabelText("New password"),
    "Changed-Pass-2026",
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Change password" }),
  );
  await waitFor(() => expect(changed).toHaveBeenCalledOnce());
  expect(JSON.parse(String(vi.mocked(fetch).mock.calls[0][1]?.body))).toEqual({
    current_password: "Temporary-Pass-2026",
    new_password: "Changed-Pass-2026",
  });
});
it("shows actor names and loads the next audit page", async () => {
  vi.mocked(fetch)
    .mockResolvedValueOnce(
      response({
        items: [
          {
            event_id: "1",
            event_type: "APPROVED",
            actor_name: "Rina",
            occurred_at: "2026-10-15T00:00:00Z",
            payload: { outstanding_issues: ["MISSING_EVIDENCE"] },
          },
        ],
        next_offset: 1,
      }),
    )
    .mockResolvedValueOnce(
      response({
        items: [
          {
            event_id: "2",
            event_type: "VOUCHER_DOWNLOADED",
            actor_name: "Budi",
            occurred_at: "2026-10-15T01:00:00Z",
            payload: {},
          },
        ],
        next_offset: null,
      }),
    );
  render(<HistoryTimeline requestId="request" />);
  await screen.findByText(/Rina/);
  expect(screen.getByText(/MISSING_EVIDENCE/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Load activity" }));
  await screen.findByText(/Budi/);
  expect(vi.mocked(fetch).mock.calls[1][0]).toBe(
    "/api/requests/request/history?offset=1",
  );
});
it("saves a reviewer and deadline together", async () => {
  vi.mocked(fetch).mockImplementation(async (url) =>
    response(
      String(url).endsWith("/reviewers")
        ? [{ user_id: "reviewer", name: "Finance Person" }]
        : {},
    ),
  );
  const saved = vi.fn();
  render(
    <AllocationPanel
      requestId="request"
      data={{ assigned_reviewer: null, due_date: null } as Detail}
      onSaved={saved}
    />,
  );
  await screen.findByRole("option", { name: "Finance Person" });
  await userEvent.selectOptions(
    screen.getByLabelText("Responsible reviewer"),
    "reviewer",
  );
  await userEvent.type(screen.getByLabelText("Due date (WIB)"), "2026-10-16");
  await userEvent.click(
    screen.getByRole("button", { name: "Save allocation" }),
  );
  await waitFor(() => expect(saved).toHaveBeenCalled());
  const call = vi
    .mocked(fetch)
    .mock.calls.find(([url]) => String(url).endsWith("/allocation"));
  expect(JSON.parse(String(call?.[1]?.body))).toEqual({
    assigned_reviewer: "reviewer",
    due_date: "2026-10-16",
  });
});
it("shows only operational role choices to System Admin", async () => {
  vi.mocked(fetch).mockResolvedValue(response([]));
  render(
    <Organization
      user={{
        user_id: "admin",
        username: "user_admin",
        role: "SYSTEM_ADMIN",
        permissions: ["users"],
      }}
    />,
  );
  await userEvent.click(await screen.findByRole("button", { name: "+ Account" }));
  expect(
    screen.getByRole("option", { name: "FINANCE_REVIEWER" }),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("option", { name: "SUPER_ADMIN" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("option", { name: "SYSTEM_ADMIN" }),
  ).not.toBeInTheDocument();
  await waitFor(() => expect(fetch).toHaveBeenCalled());
});

it("links duplicate warnings to the matched request and identifies the source invoice", () => {
  render(<MemoryRouter><ValidationWarnings issues={[{flag_type:"POSSIBLE_DUPLICATE",details:{matches:[{
    request_id:"existing-request", matched_sources:[{vendor_name:"Vendor A",invoice_number:"INV-42"}],
  }]}}]} /></MemoryRouter>);
  expect(screen.getByRole("link", {name:"Request existing-request"})).toHaveAttribute("href","/requests/existing-request");
  expect(screen.getByText("Vendor A · Invoice INV-42")).toBeInTheDocument();
});

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import Organization from "./Organization";
import { ClientManager } from "../features/clients/ClientManager";
const company = { client_id: "company-1", client_name: "Acme" };
const person = { user_id: "person-1", username: "alice", role: "CLIENT_PIC", is_active: true, assignments: [] };
const response = (body: unknown) => ({ ok: true, json: async () => body }) as Response;
beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
  vi.spyOn(window, "confirm").mockReturnValue(true);
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
it("confirms company removal and refreshes the list", async () => {
  let removed = false;
  vi.mocked(fetch).mockImplementation(async (_url, init) => {
    if (init?.method === "DELETE") { removed = true; return response(null); }
    return response(removed ? [] : [company]);
  });
  render(<ClientManager />);
  await userEvent.click(await screen.findByRole("button", { name: "Remove company Acme" }));
  await screen.findByText("Company removed.");
  expect(window.confirm).toHaveBeenCalled();
  expect(screen.queryByText("Acme")).not.toBeInTheDocument();
  expect(fetch).toHaveBeenCalledWith("/api/clients/company-1", expect.objectContaining({ method: "DELETE" }));
});
it("does nothing when removal is cancelled", async () => {
  vi.mocked(window.confirm).mockReturnValue(false);
  vi.mocked(fetch).mockResolvedValue(response([company]));
  render(<ClientManager />);
  await userEvent.click(await screen.findByRole("button", { name: "Remove company Acme" }));
  expect(screen.getByText("Acme")).toBeInTheDocument();
  expect(vi.mocked(fetch).mock.calls.every(([, init]) => init?.method !== "DELETE")).toBe(true);
});
it("removes and restores account access", async () => {
  let active = true;
  vi.mocked(fetch).mockImplementation(async (url, init) => {
    if (init?.method === "PATCH") { active = JSON.parse(init.body as string).is_active; return response({ ...person, is_active: active }); }
    return response(String(url).includes("/clients") ? [] : [{ ...person, is_active: active }]);
  });
  render(<Organization user={{ user_id: "admin", username: "admin", role: "SUPER_ADMIN" }} />);
  await userEvent.click(await screen.findByRole("button", { name: /alice.*CLIENT_PIC/ }));
  await userEvent.click(screen.getByRole("button", { name: "Remove account" }));
  await screen.findByText("Account removed. Access revoked; history preserved.");
  expect(fetch).toHaveBeenCalledWith("/api/users/person-1/status", expect.objectContaining({ method: "PATCH", body: JSON.stringify({ is_active: false }) }));
  await userEvent.click(screen.getByRole("button", { name: /alice.*CLIENT_PIC/ }));
  await userEvent.click(screen.getByRole("button", { name: "Restore account" }));
  await screen.findByText("Account restored.");
});

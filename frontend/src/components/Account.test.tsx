import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import Account from "./Account";
afterEach(() => {
  vi.unstubAllGlobals();
});
it("creates an account with separate professional and system roles and client assignments", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockImplementation(async (url) => ({
        ok: true,
        json: async () =>
          url === "/api/clients/all"
            ? [{ client_id: "c", client_name: "Acme", client_code: "ACME" }]
            : {},
      })),
  );
  render(<Account create />);
  await screen.findByText("Acme (ACME)");
  await userEvent.type(screen.getByLabelText("Username"), "rina");
  await userEvent.type(screen.getByLabelText("Full name"), "Rina Setiawan");
  await userEvent.type(
    screen.getByLabelText("Professional role / Jabatan"),
    "Admin",
  );
  await userEvent.click(screen.getByRole("checkbox"));
  await userEvent.click(screen.getByRole("button", { name: "Create account" }));
  await screen.findByRole("status");
  const call = vi
    .mocked(fetch)
    .mock.calls.find(([url]) => url === "/api/users");
  expect(JSON.parse(call![1]!.body as string)).toMatchObject({
    username: "rina",
    full_name: "Rina Setiawan",
    job_title: "Admin",
    role: "CLIENT_PIC",
    assignments: [{ client_id: "c" }],
    signature_data: null,
  });
});
it("loads an existing signature and removes it explicitly", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockImplementation(async () => ({
        ok: true,
        json: async () => ({
          full_name: "Rina",
          job_title: "Admin",
          signature_data: "data:image/png;base64,c2ln",
        }),
      })),
  );
  render(<Account />);
  await screen.findByAltText("Your uploaded signature");
  await userEvent.click(
    screen.getByRole("button", { name: "Remove signature" }),
  );
  await userEvent.click(screen.getByRole("button", { name: "Save account" }));
  await waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalledTimes(2));
  expect(
    JSON.parse(vi.mocked(fetch).mock.calls[1][1]!.body as string),
  ).toMatchObject({ signature_data: null, remove_signature: true });
});

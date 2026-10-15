import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import Chat from "./Chat";
afterEach(() => {
  vi.unstubAllGlobals();
});
const response = (body: unknown) =>
  ({ ok: true, json: async () => body }) as Response;
it("sends a scoped question and renders the grounded answer", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockImplementation(async (url) =>
        response(
          url === "/api/requests"
            ? [
                {
                  request_id: "r1",
                  client_name: "Acme",
                  status: "AMIGA_REVIEWED",
                },
              ]
            : { answer: "INV-001 is approved.", chunks_used: 4 },
        ),
      ),
  );
  render(
    <MemoryRouter initialEntries={["/chat?request_id=r1"]}>
      <Chat />
    </MemoryRouter>,
  );
  await screen.findByRole("option", { name: /Acme/ });
  await userEvent.type(
    screen.getByLabelText("Your question"),
    "Status of INV-001?",
  );
  await userEvent.click(screen.getByRole("button", { name: "Ask question" }));
  await screen.findByText("INV-001 is approved.");
  const call = vi.mocked(fetch).mock.calls.find(([url]) => url === "/api/chat");
  expect(JSON.parse(call![1]!.body as string)).toEqual({
    message: "Status of INV-001?",
    request_id: "r1",
  });
  expect(
    screen.getByText("Based on 4 retrieved document sections"),
  ).toBeInTheDocument();
});
it("preserves the question after an upstream error and allows retry", async () => {
  let attempts = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(async (url) => {
      if (url === "/api/requests") return response([]);
      attempts++;
      return attempts === 1
        ? ({
            ok: false,
            status: 503,
            json: async () => ({ detail: "Temporarily unavailable" }),
          } as Response)
        : response({ answer: "No indexed information.", chunks_used: 0 });
    }),
  );
  render(
    <MemoryRouter>
      <Chat />
    </MemoryRouter>,
  );
  await userEvent.type(
    screen.getByLabelText("Your question"),
    "Unknown invoice?",
  );
  await userEvent.click(screen.getByRole("button", { name: "Ask question" }));
  await screen.findByRole("alert");
  expect(screen.getByLabelText("Your question")).toHaveValue(
    "Unknown invoice?",
  );
  await userEvent.click(screen.getByRole("button", { name: "Ask question" }));
  await screen.findByText("No indexed information.");
  await waitFor(() =>
    expect(screen.getByLabelText("Your question")).toHaveValue(""),
  );
  expect(screen.getByText("No matching indexed records")).toBeInTheDocument();
});

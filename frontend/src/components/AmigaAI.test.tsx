import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import AmigaAI from "./AmigaAI";

afterEach(() => {
  vi.unstubAllGlobals();
});
function setup(path = "/") {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockImplementation(async (url) => ({
        ok: true,
        json: async () =>
          url === "/api/requests"
            ? [
                {
                  request_id: "r1",
                  client_name: "Acme",
                  status: "READY_FOR_REVIEW",
                },
              ]
            : {
                answer: "Invoice progress",
                chunks_used: 1,
                sources: [{ request_id: "r1", label: "Acme · INV-1" }],
                has_more_items: true,
              },
      })),
  );
  return render(
    <MemoryRouter initialEntries={[path]}>
      <div>
        <button>Workspace action</button>
      </div>
      <AmigaAI />
    </MemoryRouter>,
  );
}
it("opens an accessible dialog, traps focus, restores focus, and preserves drafts and conversation", async () => {
  setup();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  const launcher = screen.getByRole("button", { name: "Open Amiga AI" });
  await userEvent.click(launcher);
  const dialog = screen.getByRole("dialog", { name: "Amiga AI" });
  expect(document.body.style.overflow).toBe("hidden");
  const close = within(dialog).getByRole("button", { name: "Close Amiga AI" });
  expect(close).toHaveFocus();
  await userEvent.tab({ shift: true });
  expect(within(dialog).getByLabelText("Your question")).toHaveFocus();
  await userEvent.type(
    screen.getByLabelText("Your question"),
    "Current progress?",
  );
  await userEvent.click(screen.getByRole("button", { name: "Ask question" }));
  await screen.findByText("Invoice progress");
  await userEvent.type(screen.getByLabelText("Your question"), "A draft");
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(document.body.style.overflow).toBe("");
  expect(launcher).toHaveFocus();
  await userEvent.click(launcher);
  expect(screen.getByText("Invoice progress")).toBeInTheDocument();
  expect(screen.getByLabelText("Your question")).toHaveValue("A draft");
});
it("opens legacy deep links with scope and sends line-item pagination", async () => {
  setup("/chat?request_id=r1");
  await screen.findByRole("dialog", { name: "Amiga AI" });
  await screen.findByRole("option", { name: /Acme/ });
  expect(screen.getByLabelText("Search scope")).toHaveValue("r1");
  await userEvent.type(screen.getByLabelText("Your question"), "List items");
  await userEvent.click(screen.getByRole("button", { name: "Ask question" }));
  await screen.findByText("Invoice progress");
  await userEvent.click(
    screen.getByRole("button", { name: "Next line items" }),
  );
  await waitFor(() =>
    expect(
      vi.mocked(fetch).mock.calls.filter(([url]) => url === "/api/chat"),
    ).toHaveLength(2),
  );
  const calls = vi
    .mocked(fetch)
    .mock.calls.filter(([url]) => url === "/api/chat");
  expect(JSON.parse(calls[1][1]!.body as string)).toEqual({
    message: "List items",
    request_id: "r1",
    item_page: 1,
  });
});
it("defaults to the current request and clears the draft on scope changes", async () => {
  setup("/requests/r1");
  await userEvent.click(screen.getByRole("button", { name: "Open Amiga AI" }));
  await screen.findByRole("option", { name: /Acme/ });
  expect(screen.getByLabelText("Search scope")).toHaveValue("r1");
  await userEvent.type(screen.getByLabelText("Your question"), "Draft");
  await userEvent.selectOptions(screen.getByLabelText("Search scope"), "");
  expect(screen.getByLabelText("Your question")).toHaveValue("");
});
it("dismisses with Escape even when submitting has moved focus outside the dialog", async () => {
  setup();
  await userEvent.click(screen.getByRole("button", { name: "Open Amiga AI" }));
  await userEvent.type(screen.getByLabelText("Your question"), "Current status?");
  await userEvent.click(screen.getByRole("button", { name: "Ask question" }));
  await screen.findByText("Invoice progress");
  (document.activeElement as HTMLElement)?.blur();
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

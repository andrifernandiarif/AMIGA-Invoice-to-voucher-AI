import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import TemplateEditor from "./TemplateEditor";
afterEach(() => {
  vi.unstubAllGlobals();
});
it("loads the provided HTML and saves edits as a new active version", async () => {
  const content =
    "<h1>{{ vendor_name }}</h1>{{ invoice_number }}{{ line_items_table }}{{ authorization_disclaimer }}";
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url, init?: RequestInit) => ({
      ok: true,
      json: async () =>
        init?.method === "POST"
          ? { template_id: "t" }
          : { templates: [], default_template: content },
    })),
  );
  render(<TemplateEditor />);
  expect(await screen.findByLabelText("Template HTML")).toHaveValue(content);
  await userEvent.type(screen.getByLabelText("New version name"), "custom-v1");
  await userEvent.click(
    screen.getByRole("button", { name: "Save & activate template" }),
  );
  await screen.findByRole("status");
  const call = vi
    .mocked(fetch)
    .mock.calls.find(([, init]) => init?.method === "POST");
  await waitFor(() => expect(call).toBeDefined());
  const body = call?.[1]?.body as FormData;
  expect(body.get("version")).toBe("custom-v1");
  expect(body.get("template_content")).toBe(content);
});

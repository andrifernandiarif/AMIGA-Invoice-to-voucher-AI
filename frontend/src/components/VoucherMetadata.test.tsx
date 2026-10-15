import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import VoucherMetadata from "./VoucherMetadata";
afterEach(() => {
  vi.unstubAllGlobals();
});
it("saves purpose and multiple external approvers, and tracks unsaved changes", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) }),
  );
  const dirty = vi.fn(),
    saved = vi.fn();
  render(
    <VoucherMetadata
      requestId="r"
      purpose="Travel"
      approvers={[{ full_name: "Budi", job_title: "BOD" }]}
      editable
      onDirty={dirty}
      onSaved={saved}
    />,
  );
  await userEvent.type(
    screen.getByLabelText("Peruntukan / Expense purpose"),
    " expenses",
  );
  expect(dirty).toHaveBeenCalledWith(true);
  await userEvent.click(
    screen.getByRole("button", { name: "+ Add another approver" }),
  );
  await userEvent.type(screen.getAllByLabelText("Full name")[1], "Sari");
  await userEvent.type(
    screen.getAllByLabelText("Professional role / Jabatan")[1],
    "Manager",
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Save upload details" }),
  );
  await waitFor(() => expect(saved).toHaveBeenCalledOnce());
  expect(dirty).toHaveBeenLastCalledWith(false);
  const [, request] = vi.mocked(fetch).mock.calls[0];
  expect(JSON.parse(request!.body as string)).toEqual({
    voucher_purpose: "Travel expenses",
    approvers: [
      { full_name: "Budi", job_title: "BOD" },
      { full_name: "Sari", job_title: "Manager" },
    ],
  });
});
it("locks metadata during finance review", () => {
  render(
    <VoucherMetadata
      requestId="r"
      purpose="Travel"
      approvers={[{ full_name: "Budi", job_title: "BOD" }]}
      editable={false}
      onDirty={vi.fn()}
      onSaved={vi.fn()}
    />,
  );
  expect(screen.getByLabelText("Peruntukan / Expense purpose")).toBeDisabled();
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
});

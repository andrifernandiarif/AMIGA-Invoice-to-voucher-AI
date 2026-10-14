import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import TaxEditor from "./TaxEditor";
import { TaxOption } from "../api";
const options: TaxOption[] = [
  {
    id: "PPH23_SERVICES",
    tax_type: "PPH23",
    label: "PPh 23 — jasa/sewa harta, 2%",
    rate_percent: "2",
    note: "Jasa yang sesuai.",
  },
  {
    id: "PPH21_MANUAL",
    tax_type: "PPH21",
    label: "PPh 21 — tarif manual / TER",
    rate_percent: null,
    note: "Masukkan tarif.",
  },
];
afterEach(() => {
  vi.unstubAllGlobals();
});
it("calculates a preset and saves it as an added tax row", async () => {
  const saved = vi.fn(async () => {});
  const dirty = vi.fn();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: true,
      json: async () => ({
        tax_assignment: {
          tax_type: "PPH23",
          profile: "PPH23_SERVICES",
          rate_percent: "2",
          base_amount: "100",
          base_is_manual: false,
          effect: "ADD",
          amount: "2",
        },
      }),
    })),
  );
  render(
    <TaxEditor
      requestId="request"
      itemId="item"
      lineTotal="100"
      options={options}
      editable
      onDirty={dirty}
      onSaved={saved}
      onSaving={() => {}}
    />,
  );
  await userEvent.selectOptions(
    screen.getByLabelText("Tax assignment for item item"),
    "PPH23_SERVICES",
  );
  expect(screen.getByLabelText("Tax rate for item item")).toHaveValue(2);
  expect(screen.getByLabelText("Tax rate for item item")).toBeDisabled();
  expect(screen.getByText("Additional tax row")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Apply tax" }));
  await waitFor(() => expect(saved).toHaveBeenCalled());
  const body = JSON.parse(vi.mocked(fetch).mock.calls[0][1]?.body as string);
  expect(body).toMatchObject({
    profile: "PPH23_SERVICES",
    rate_percent: "2",
    base_amount: null,
    effect: "ADD",
  });
  expect(dirty).toHaveBeenCalledWith("item", true);
});
it("requires a manual rate and lets the user choose a base and withholding", async () => {
  render(
    <TaxEditor
      requestId="request"
      itemId="item"
      lineTotal="100"
      options={options}
      editable
      onDirty={() => {}}
      onSaved={async () => {}}
      onSaving={() => {}}
    />,
  );
  await userEvent.selectOptions(
    screen.getByLabelText("Tax assignment for item item"),
    "PPH21_MANUAL",
  );
  expect(screen.getByRole("button", { name: "Apply tax" })).toBeDisabled();
  await userEvent.type(screen.getByLabelText("Tax rate for item item"), "5");
  await userEvent.click(screen.getByLabelText("Enter tax base manually"));
  await userEvent.clear(screen.getByLabelText("Tax base for item item"));
  await userEvent.type(screen.getByLabelText("Tax base for item item"), "50");
  await userEvent.selectOptions(
    screen.getByLabelText("Voucher treatment"),
    "DEDUCT",
  );
  expect(screen.getByText("Deduction")).toBeInTheDocument();
  expect(screen.getByText("2,5")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Apply tax" })).toBeEnabled();
});

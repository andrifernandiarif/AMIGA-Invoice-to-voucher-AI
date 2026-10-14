import { render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import VoucherDocument from "./VoucherDocument";
afterEach(() => {
  vi.unstubAllGlobals();
});
it("previews the actual voucher PDF without triggering a download", async () => {
  vi.stubGlobal(
    "URL",
    class extends URL {
      static createObjectURL = vi.fn(() => "blob:voucher");
      static revokeObjectURL = vi.fn();
    },
  );
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: true,
      blob: async () => new Blob(["%PDF-test"]),
    })),
  );
  const view = render(<VoucherDocument requestId="request-id" />);
  expect(await screen.findByTitle("Voucher")).toHaveAttribute(
    "src",
    "blob:voucher",
  );
  expect(fetch).toHaveBeenCalledWith(
    "/api/requests/request-id/voucher-pdf?preview=true",
    expect.anything(),
  );
  view.unmount();
  expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:voucher");
});

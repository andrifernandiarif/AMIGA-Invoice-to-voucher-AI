import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
vi.mock("react-pdf", async () => {
  const { useEffect } = await import("react");
  return {
    pdfjs: { GlobalWorkerOptions: {} },
    Document: function Document({children, file, onLoadSuccess}: any) {
      useEffect(() => { queueMicrotask(() => onLoadSuccess({numPages: 3})); }, [file]);
      return <div>{children}</div>;
    },
    Page: function Page({pageNumber, onRenderSuccess}: any) {
      useEffect(() => { onRenderSuccess(); }, [pageNumber]);
      return <div>Rendered invoice page {pageNumber}</div>;
    },
  };
});
import PdfEvidence from "./PdfEvidence";
it("reports rendered evidence, paginates, and resets when the selected file changes", async () => {
  const ready = vi.fn();
  const {rerender} = render(<PdfEvidence url="blob:first" onReady={ready} />);
  expect(await screen.findByText("Page 1 of 3")).toBeInTheDocument();
  expect(ready).toHaveBeenCalled();
  expect(screen.getByRole("button",{name:"Previous invoice page"})).toBeDisabled();
  await userEvent.click(screen.getByRole("button",{name:"Next invoice page"}));
  expect(screen.getByText("Rendered invoice page 2")).toBeInTheDocument();
  rerender(<PdfEvidence url="blob:second" onReady={ready} />);
  await waitFor(()=>expect(screen.getByText("Page 1 of 3")).toBeInTheDocument());
});

import { useEffect, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

export default function PdfEvidence({ url, onReady }: { url: string; onReady: () => void }) {
  const container = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(600);
  const [pages, setPages] = useState(0);
  const [page, setPage] = useState(1);
  const [error, setError] = useState(false);
  useEffect(() => {
    setPage(1); setPages(0); setError(false);
  }, [url]);
  useEffect(() => {
    const element = container.current;
    if (!element) return;
    const measure = () => setWidth(Math.max(100, element.clientWidth));
    measure();
    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", measure);
      return () => window.removeEventListener("resize", measure);
    }
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return <div ref={container} className="pdf-evidence" aria-label="Original invoice">
    {pages > 1 && <div className="pdf-pagination">
      <button className="secondary" aria-label="Previous invoice page" disabled={page === 1} onClick={() => setPage(p => p - 1)}>Previous</button>
      <span>Page {page} of {pages}</span>
      <button className="secondary" aria-label="Next invoice page" disabled={page === pages} onClick={() => setPage(p => p + 1)}>Next</button>
    </div>}
    <Document file={url} onLoadSuccess={({ numPages }) => setPages(numPages)} onLoadError={() => setError(true)}
      loading={<p className="muted">Loading invoice page…</p>}
      error={<p role="alert">The invoice preview could not load. Open the original document below.</p>}>
      <Page pageNumber={page} width={width} renderTextLayer={false} renderAnnotationLayer={false}
        onRenderSuccess={onReady} onRenderError={() => setError(true)}
        loading={<p className="muted">Rendering invoice page…</p>} />
    </Document>
    {error && <a href={url} target="_blank" rel="noreferrer">Open original invoice ↗</a>}
  </div>;
}

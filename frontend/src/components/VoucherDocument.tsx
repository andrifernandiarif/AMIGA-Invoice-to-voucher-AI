import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "../api";

export default function VoucherDocument({ requestId }: { requestId: string }) {
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const reload = useCallback(() => setRetry((n) => n + 1), []);
  useEffect(() => {
    let cancelled = false;
    let objectUrl = "";
    setUrl("");
    setError("");
    apiFetch(`/api/requests/${requestId}/voucher-pdf?preview=true`)
      .then((r) => r.blob())
      .then((blob) => {
        if (!cancelled) {
          objectUrl = URL.createObjectURL(blob);
          setUrl(objectUrl);
        }
      })
      .catch((e) => {
        if (!cancelled) setError(e.message);
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [requestId, retry]);
  if (error)
    return (
      <div className="empty-state">
        <p role="alert">{error}</p>
        <button className="secondary" onClick={reload}>
          Retry preview
        </button>
      </div>
    );
  if (!url) return <div className="empty-state">Loading voucher…</div>;
  return (
    <div className="voucher-document">
      <iframe title="Voucher" src={url} />
      <a className="text-button" href={url} target="_blank" rel="noreferrer">
        Open voucher in a new tab ↗
      </a>
    </div>
  );
}

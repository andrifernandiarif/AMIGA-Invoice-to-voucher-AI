import { FormEvent, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, Invoice, json } from "../api";

type Turn = { question: string; answer: string; chunks: number };
export default function Chat() {
  const [params] = useSearchParams();
  const [requestId, setRequestId] = useState(params.get("request_id") || "");
  const [requests, setRequests] = useState<Invoice[]>([]);
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [pending, setPending] = useState("");
  const [error, setError] = useState("");
  const controller = useRef<AbortController | null>(null);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const abort = new AbortController();
    api<Invoice[]>("/requests", { signal: abort.signal })
      .then(setRequests)
      .catch((e) => {
        if (!abort.signal.aborted) setError(e.message);
      });
    return () => {
      abort.abort();
      controller.current?.abort();
    };
  }, []);
  useEffect(() => {
    end.current?.scrollIntoView?.({ behavior: "smooth", block: "nearest" });
  }, [turns, pending]);
  async function submit(e: FormEvent) {
    e.preventDefault();
    const message = question.trim();
    if (!message || pending) return;
    setError("");
    setPending(message);
    const abort = new AbortController();
    controller.current = abort;
    try {
      const result = await api<{ answer: string; chunks_used: number }>(
        "/chat",
        {
          ...json({ message, ...(requestId ? { request_id: requestId } : {}) }),
          signal: abort.signal,
        },
      );
      setTurns((previous) => [
        ...previous,
        {
          question: message,
          answer: result.answer,
          chunks: result.chunks_used,
        },
      ]);
      setQuestion("");
    } catch (e) {
      if (!abort.signal.aborted) setError((e as Error).message);
    } finally {
      if (!abort.signal.aborted) setPending("");
    }
  }
  return (
    <>
      <div className="page-heading">
        <div>
          <span className="eyebrow">WORKSPACE / INVOICE CHAT</span>
          <h1>
            Ask about your invoices<span className="heading-dot">.</span>
          </h1>
          <p>Find details in approved vouchers and their invoice records.</p>
        </div>
      </div>
      <section className="card chat-workspace">
        <div className="chat-context">
          <label>
            Search scope
            <select
              aria-label="Search scope"
              value={requestId}
              disabled={!!pending}
              onChange={(e) => {
                setRequestId(e.target.value);
                setTurns([]);
                setError("");
              }}
            >
              <option value="">All requests assigned to me</option>
              {requestId &&
                !requests.some((r) => r.request_id === requestId) && (
                  <option value={requestId}>
                    Selected request ({requestId.slice(0, 8)})
                  </option>
                )}
              {requests.map((r) => (
                <option key={r.request_id} value={r.request_id}>
                  {r.client_name} · {r.request_id.slice(0, 8)} · {r.status}
                </option>
              ))}
            </select>
          </label>
          <p className="muted">
            Approved and delivered requests are searchable. Include the
            invoice number in each question; each question is answered
            independently. Results may cover only part of your records.
          </p>
        </div>
        <div className="chat-messages" aria-live="polite" aria-busy={!!pending}>
          {!turns.length && !pending && (
            <div className="chat-intro">
              <h3>What would you like to find?</h3>
              <p>
                Try a question below, or choose a request to focus your search.
              </p>
              <div className="chat-examples">
                {[
                  "What are the invoice numbers and their current statuses?",
                  "What are the extracted amount and calculated voucher total?",
                  "Which taxes are assigned to the invoice items?",
                ].map((text) => (
                  <button
                    type="button"
                    className="secondary"
                    key={text}
                    onClick={() => setQuestion(text)}
                  >
                    {text}
                  </button>
                ))}
              </div>
            </div>
          )}
          {turns.map((turn, i) => (
            <div className="chat-turn" key={i}>
              <div className="chat-question">
                <strong>You</strong>
                <p>{turn.question}</p>
              </div>
              <div className="chat-answer">
                <strong>Invoice assistant</strong>
                <p>{turn.answer}</p>
                <small className="muted">
                  {turn.chunks
                    ? `Based on ${turn.chunks} retrieved document sections`
                    : "No matching indexed records"}
                </small>
              </div>
            </div>
          ))}
          {pending && (
            <div className="chat-turn">
              <div className="chat-question">
                <strong>You</strong>
                <p>{pending}</p>
              </div>
              <p role="status">Searching your invoice records…</p>
            </div>
          )}
          <div ref={end} />
        </div>
        {error && (
          <div className="error-banner" role="alert">
            {error} Your question is kept below so you can retry.
          </div>
        )}
        <form className="chat-composer" onSubmit={submit}>
          <label htmlFor="chat-question">Your question</label>
          <textarea
            id="chat-question"
            required
            maxLength={2000}
            rows={3}
            value={question}
            disabled={!!pending}
            placeholder="Ask about an invoice, its amounts, taxes, or current status…"
            onChange={(e) => setQuestion(e.target.value)}
          />
          <div className="chat-composer-actions">
            <small className="muted">{question.length}/2000</small>
            <button
              className="primary"
              disabled={!!pending || !question.trim()}
            >
              {pending ? "Searching…" : "Ask question"}
            </button>
          </div>
        </form>
      </section>
    </>
  );
}

import { FormEvent, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, Invoice, json } from "../api";

type Source = { request_id?: string; client_id?: string; label: string };
type Turn = {
  question: string;
  answer: string;
  chunks: number;
  sources?: Source[];
  coverage?: string;
  requestId?: string;
  page: number;
  hasMore?: boolean;
};
export default function Chat({ scope }: { scope?: string } = {}) {
  const [params] = useSearchParams();
  const [requestId, setRequestId] = useState(
    scope ?? params.get("request_id") ?? "",
  );
  useEffect(() => {
    if (scope !== undefined) {
      controller.current?.abort();
      setPending("");
      setRequestId(scope);
      setTurns([]);
      setQuestion("");
      setError("");
    }
  }, [scope]);
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
    end.current?.scrollIntoView?.({
      behavior: window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
        ? "auto"
        : "smooth",
      block: "nearest",
    });
  }, [turns, pending]);
  async function submit(e: FormEvent) {
    e.preventDefault();
    await send(question.trim(), requestId);
  }
  async function send(message: string, selectedRequest = "", page = 0) {
    if (!message || pending) return;
    setError("");
    setQuestion(message);
    setPending(message);
    const abort = new AbortController();
    controller.current = abort;
    try {
      const result = await api<{
        answer: string;
        chunks_used: number;
        sources?: Source[];
        coverage?: string;
        has_more_items?: boolean;
      }>("/chat", {
        ...json({
          message,
          ...(selectedRequest ? { request_id: selectedRequest } : {}),
          ...(page ? { item_page: page } : {}),
        }),
        signal: abort.signal,
      });
      if (abort.signal.aborted) return;
      const sourceIds = [
        ...new Set(
          result.sources?.flatMap((source) =>
            source.request_id ? [source.request_id] : [],
          ) || [],
        ),
      ];
      setTurns((previous) => [
        ...previous,
        {
          question: message,
          answer: result.answer,
          chunks: result.chunks_used,
          sources: result.sources,
          coverage: result.coverage,
          requestId: sourceIds.length === 1 ? sourceIds[0] : undefined,
          page,
          hasMore: result.has_more_items,
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
                setQuestion("");
                setError("");
              }}
            >
              <option value="">All accessible requests</option>
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
            Search current company, invoice, and progress records within your
            permissions. Include a company name or invoice number; each question
            is answered independently.
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
                  "What has happened so far, and what is the next required action?",
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
                <strong>Amiga AI</strong>
                <p>{turn.answer}</p>
                <small className="muted">
                  {turn.chunks
                    ? `Based on ${turn.chunks} authorized records`
                    : "No matching accessible records"}
                </small>
                {turn.hasMore && turn.requestId && (
                  <button
                    className="secondary"
                    disabled={!!pending}
                    onClick={() =>
                      send(turn.question, turn.requestId, turn.page + 1)
                    }
                  >
                    Next line items
                  </button>
                )}
                {turn.coverage && (
                  <p className="chat-coverage">{turn.coverage}</p>
                )}
                {!!turn.sources?.length && (
                  <div className="chat-sources" aria-label="Sources">
                    {turn.sources.map((source, index) =>
                      source.request_id ? (
                        <Link key={index} to={`/requests/${source.request_id}`}>
                          {source.label}
                        </Link>
                      ) : (
                        <span key={index}>{source.label}</span>
                      ),
                    )}
                  </div>
                )}
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

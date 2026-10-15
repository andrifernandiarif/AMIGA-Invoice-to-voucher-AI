import { useCallback, useEffect, useState } from "react";
import { api } from "../../api";
import { errorMessage } from "../../utils/errors";

export type HistoryEvent = {
  event_id: string;
  event_type: string;
  actor_name: string;
  occurred_at: string;
  payload: unknown;
};

export function useHistoryTimeline(requestId: string) {
  const [items, setItems] = useState<HistoryEvent[]>([]);
  const [nextOffset, setNextOffset] = useState<number | null>(0);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (offset: number) => {
    setBusy(true);
    setError("");
    try {
      const data = await api<{ items: HistoryEvent[]; next_offset: number | null }>(
        `/requests/${requestId}/history?offset=${offset}`,
      );
      setItems((previous) => (offset ? [...previous, ...data.items] : data.items));
      setNextOffset(data.next_offset);
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }, [requestId]);

  useEffect(() => {
    setItems([]);
    void load(0);
  }, [load]);

  return { items, nextOffset, error, busy, load };
}

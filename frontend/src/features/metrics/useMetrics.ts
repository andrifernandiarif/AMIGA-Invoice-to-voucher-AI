import { useEffect, useState } from "react";
import { api } from "../../api";
import { errorMessage } from "../../utils/errors";

export type Metric = {
  metric_type: string;
  count: number;
  breaches: number;
  breach_rate: number;
  p50: number;
  p90: number;
  p99: number;
};
export type Breach = {
  metric_type: string;
  duration_ms: number;
  threshold_ms: number;
  measured_at: string;
};

export function useMetrics() {
  const [metrics, setMetrics] = useState<Metric[]>([]);
  const [breaches, setBreaches] = useState<Breach[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let live = true;
    async function load() {
      try {
        const [nextMetrics, nextBreaches] = await Promise.all([
          api<Metric[]>("/telemetry/metrics"),
          api<Breach[]>("/telemetry/breaches"),
        ]);
        if (live) {
          setMetrics(nextMetrics);
          setBreaches(nextBreaches);
          setError("");
        }
      } catch (error) {
        if (live) setError(errorMessage(error));
      } finally {
        if (live) setLoading(false);
      }
    }
    void load();
    const timer = window.setInterval(() => void load(), 60000);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, []);

  return { metrics, breaches, error, loading };
}

export function Feedback({
  message,
  tone = "error",
}: {
  message: string;
  tone?: "error" | "success" | "info";
}) {
  if (!message) return null;
  return (
    <p className={`feedback feedback-${tone}`} role={tone === "error" ? "alert" : "status"}>
      {message}
    </p>
  );
}

export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return <p className="loading-state" role="status">{label}</p>;
}

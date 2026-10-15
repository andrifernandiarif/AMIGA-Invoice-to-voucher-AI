export function Skeleton({
  className = "",
}: {
  className?: string;
}) {
  return <span className={`skeleton ${className}`.trim()} aria-hidden="true" />;
}

export function PageSkeleton({
  variant = "content",
}: {
  variant?: "content" | "dashboard" | "form";
}) {
  if (variant === "dashboard") {
    return (
      <div className="page-skeleton" role="status" aria-label="Loading page">
        <div className="skeleton-stats">
          {[1, 2, 3, 4].map((item) => (
            <div className="card skeleton-card" key={item}>
              <Skeleton className="skeleton-line skeleton-line-short" />
              <Skeleton className="skeleton-line skeleton-line-value" />
              <Skeleton className="skeleton-line skeleton-line-medium" />
            </div>
          ))}
        </div>
        <div className="card skeleton-card skeleton-panel">
          <Skeleton className="skeleton-line skeleton-line-heading" />
          <Skeleton className="skeleton-line skeleton-line-medium" />
          <div className="skeleton-table">
            {[1, 2, 3, 4, 5].map((item) => (
              <Skeleton className="skeleton-line" key={item} />
            ))}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="page-skeleton" role="status" aria-label="Loading page">
      <div className="card skeleton-card skeleton-panel">
        <Skeleton className="skeleton-line skeleton-line-heading" />
        <Skeleton className="skeleton-line skeleton-line-medium" />
        {variant === "form" ? (
          <div className="skeleton-form">
            {[1, 2, 3, 4].map((item) => (
              <Skeleton className="skeleton-line skeleton-input" key={item} />
            ))}
          </div>
        ) : (
          <div className="skeleton-table">
            {[1, 2, 3, 4, 5, 6].map((item) => (
              <Skeleton className="skeleton-line" key={item} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

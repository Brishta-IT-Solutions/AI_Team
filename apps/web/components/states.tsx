import type { ApiError } from "@/lib/api";

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="state" role="status" aria-live="polite">
      <span className="pulse" aria-hidden /> {label}…
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="state state-empty">
      <p className="state-title">{title}</p>
      {children}
    </div>
  );
}

/** Loading, permission-denied, not-found and retryable states share one surface (FR-07..13). */
export function ErrorState({ error, onRetry }: { error: ApiError; onRetry?: () => void }) {
  const title =
    error.status === 403 ? "You don’t have access to this" :
    error.status === 404 ? "Not found" :
    error.status === 0 ? "Can’t reach the Control API" : "Something went wrong";
  return (
    <div className="state state-error" role="alert">
      <p className="state-title">{title}</p>
      <p className="muted">{error.body.message}</p>
      {error.body.correlation_id && <p className="mono muted small">ref {error.body.correlation_id}</p>}
      {error.body.retryable || error.status === 0 ? (
        onRetry && <button className="btn" onClick={onRetry}>Retry</button>
      ) : null}
    </div>
  );
}

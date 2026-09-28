import type { ExecutionStatus, TaskCard } from "@/lib/types";

const STATUS_TONE: Record<ExecutionStatus, string> = {
  IDLE: "neutral", QUEUED: "info", RUNNING: "info", BLOCKED: "warn", PAUSED: "warn", FAILED: "danger",
};

/** Text-first status: color is never the only cue (NFR-06). */
export function StatusBadge({ status }: { status: ExecutionStatus }) {
  if (status === "IDLE") return null;
  return <span className={`badge tone-${STATUS_TONE[status]}`}>{status.toLowerCase()}</span>;
}

export function PriorityBadge({ priority }: { priority: TaskCard["priority"] }) {
  return <span className={`badge prio prio-${priority}`} aria-label={`Priority ${priority}`}>{priority}</span>;
}

export function Elapsed({ since }: { since: string }) {
  const ms = Date.now() - new Date(since).getTime();
  const m = Math.max(0, Math.floor(ms / 60000));
  const text = m < 60 ? `${m}m` : m < 1440 ? `${Math.floor(m / 60)}h` : `${Math.floor(m / 1440)}d`;
  return <time dateTime={since} title={new Date(since).toLocaleString()}>{text}</time>;
}

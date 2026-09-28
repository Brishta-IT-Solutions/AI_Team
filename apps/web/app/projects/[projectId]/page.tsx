"use client";

import Link from "next/link";
import { use, useCallback, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { useEventCursor, useResource } from "@/lib/useResource";
import type { Board, Project, TaskCard } from "@/lib/types";
import { STAGE_LABEL } from "@/lib/types";
import { Elapsed, PriorityBadge, StatusBadge } from "@/components/badges";
import { ErrorState, Loading } from "@/components/states";

/**
 * Moving a card is a request to run a command, never a direct stage write (FR-08, AT-17).
 * Only Backlog → Analysis maps to a command; every other drop is explained, not applied.
 */
function commandForMove(card: TaskCard, target: string): { command?: string; why?: string } {
  if (card.column === target) return {};
  if (card.column === "Backlog" && target === "Analysis") return { command: "analyze" };
  const explain: Record<string, string> = {
    Approval: "Tickets reach Approval when the BA specification validates.",
    Development: "Development starts after a human approves the requirements.",
    QA: "QA starts only after the developer’s self-check passes on a commit.",
    Merge: "Merge approval opens only after independent QA passes on the current head.",
    Done: "A ticket is Done only after GitHub confirms the approved merge.",
    Backlog: "Tickets don’t move backwards; cancel and open a linked ticket instead.",
    Analysis: "Use Request changes on the ticket to send requirements back to analysis.",
  };
  return { why: explain[target] ?? "That move isn’t a permitted command." };
}

export default function ProjectBoard({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = use(params);
  const project = useResource<Project>(`/projects/${projectId}`);
  const board = useResource<Board>(`/projects/${projectId}/board`);
  const refreshBoard = board.refresh;
  const onEvents = useCallback(() => void refreshBoard(), [refreshBoard]);
  const sync = useEventCursor(projectId, onEvents);
  const [notice, setNotice] = useState<{ title: string; reasons: string[] } | null>(null);
  const [dragging, setDragging] = useState<TaskCard | null>(null);
  const [over, setOver] = useState<string | null>(null);

  async function move(card: TaskCard, target: string) {
    const { command, why } = commandForMove(card, target);
    if (why) return setNotice({ title: `Can’t move ${card.key} to ${target}`, reasons: [why] });
    if (!command) return;
    try {
      await api.post(`/tasks/${card.id}/commands`, { command, expected_version: card.version });
      setNotice(null);
      await board.refresh();
    } catch (e) {
      if (e instanceof ApiError) setNotice({ title: e.body.message, reasons: e.reasons });
    }
  }

  if (project.error) return <ErrorState error={project.error} onRetry={project.refresh} />;

  return (
    <>
      <div className="page-head">
        <div>
          <p className="eyebrow"><Link href="/">Projects</Link> / {project.data?.key ?? "…"}</p>
          <h1>{project.data?.name ?? "Loading…"}</h1>
        </div>
        <div className="row">
          <span className="small muted" aria-live="polite">
            {sync.connected ? (sync.lastSync ? <>Synced {sync.lastSync.toLocaleTimeString()}</> : "Connecting…")
              : "Reconnecting — showing last known state"}
          </span>
          <NewTicket projectId={projectId} onCreated={board.refresh} />
        </div>
      </div>

      {notice && (
        <div className="notice" role="alert" style={{ marginBottom: 16 }}>
          <div className="spread">
            <strong>{notice.title}</strong>
            <button className="btn" onClick={() => setNotice(null)}>Dismiss</button>
          </div>
          {notice.reasons.length > 0 && <ul>{notice.reasons.map((r) => <li key={r}>{r}</li>)}</ul>}
        </div>
      )}

      {board.loading ? <Loading label="Loading board" /> : board.error ? (
        <ErrorState error={board.error} onRetry={board.refresh} />
      ) : (
        <div className="board">
          {board.data?.columns.map((col) => (
            <section
              key={col.name}
              aria-label={`${col.name}, ${col.items.length} tickets`}
              className={`column ${over === col.name ? "drop-target" : ""}`}
              onDragOver={(e) => { e.preventDefault(); setOver(col.name); }}
              onDragLeave={() => setOver(null)}
              onDrop={(e) => { e.preventDefault(); setOver(null); if (dragging) void move(dragging, col.name); }}
            >
              <div className="column-head">
                <h2>{col.name}</h2>
                <span className="small muted">{col.items.length}</span>
              </div>
              {col.items.map((card) => (
                <article key={card.id} className="card ticket" draggable
                  onDragStart={() => setDragging(card)} onDragEnd={() => setDragging(null)}>
                  <div className="ticket-meta">
                    <span className="mono">{card.key}</span>
                    <PriorityBadge priority={card.priority} />
                    <StatusBadge status={card.execution_status} />
                    {card.stage === "CANCELLED" && <span className="badge tone-neutral">cancelled</span>}
                  </div>
                  <Link href={`/tasks/${card.id}`} className="ticket-title" style={{ display: "block" }}>
                    {card.title}
                  </Link>
                  <div className="ticket-meta spread">
                    <span>{STAGE_LABEL[card.stage]}</span>
                    <Elapsed since={card.updated_at} />
                  </div>
                  <label className="sr-only" htmlFor={`move-${card.id}`}>Move {card.key}</label>
                  <select id={`move-${card.id}`} className="small" style={{ marginTop: 8, width: "100%" }}
                    value="" onChange={(e) => e.target.value && void move(card, e.target.value)}>
                    <option value="">Move to…</option>
                    {board.data?.columns.filter((c) => c.name !== col.name).map((c) => (
                      <option key={c.name} value={c.name}>{c.name}</option>
                    ))}
                  </select>
                </article>
              ))}
            </section>
          ))}
        </div>
      )}
    </>
  );
}

function NewTicket({ projectId, onCreated }: { projectId: string; onCreated: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [priority, setPriority] = useState("P2");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);

  if (!open) return <button className="btn btn-primary" onClick={() => setOpen(true)}>New ticket</button>;
  return (
    <form className="card pad stack" style={{ width: 360 }} onSubmit={async (e) => {
      e.preventDefault();
      setBusy(true);
      try {
        await api.post(`/projects/${projectId}/tasks`, { title, priority, description });
        setOpen(false); setTitle(""); setDescription(""); setError(null);
        await onCreated();
      } catch (err) {
        setError(err instanceof ApiError ? err : null);
      } finally { setBusy(false); }
    }}>
      <label className="field">Title
        <input required maxLength={160} value={title} onChange={(e) => setTitle(e.target.value)} autoFocus />
      </label>
      <label className="field">Priority
        <select value={priority} onChange={(e) => setPriority(e.target.value)}>
          {["P0", "P1", "P2", "P3"].map((p) => <option key={p}>{p}</option>)}
        </select>
      </label>
      <label className="field">Description
        <textarea maxLength={20000} value={description} onChange={(e) => setDescription(e.target.value)} />
      </label>
      {error && <p className="notice small" role="alert">{error.body.message}</p>}
      <div className="row">
        <button className="btn btn-primary" disabled={busy}>Create</button>
        <button type="button" className="btn" onClick={() => setOpen(false)}>Cancel</button>
      </div>
    </form>
  );
}

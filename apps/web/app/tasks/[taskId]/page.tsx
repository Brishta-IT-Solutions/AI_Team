"use client";

import Link from "next/link";
import { use, useCallback, useRef, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { useEventCursor, useResource } from "@/lib/useResource";
import { AntigravityBrief, CodeTab, CostsTab, QATab, RunsTab, UXTab } from "@/components/work";
import type { AuditItem, PermittedAction, TaskView } from "@/lib/types";
import { STAGE_LABEL } from "@/lib/types";
import { Elapsed, PriorityBadge, StatusBadge } from "@/components/badges";
import { Empty, ErrorState, Loading } from "@/components/states";

const TABS = ["Overview", "Requirements", "UX", "Code", "QA", "Runs", "Costs", "Audit"] as const;
type Tab = (typeof TABS)[number];


export default function TicketPage({ params }: { params: Promise<{ taskId: string }> }) {
  const { taskId } = use(params);
  const { data, error, loading, refresh } = useResource<TaskView>(`/tasks/${taskId}`);
  const [tab, setTab] = useState<Tab>("Overview");
  // Committed events (a team member moving the ticket) refresh every open panel.
  const onEvents = useCallback(() => window.dispatchEvent(new Event("aitc:refresh")), []);
  useEventCursor(data?.task.project_id ?? null, onEvents);

  if (loading) return <Loading label="Loading ticket" />;
  if (error || !data) return error ? <ErrorState error={error} onRetry={refresh} /> : null;
  const t = data.task;

  return (
    <>
      <div className="page-head">
        <div>
          <p className="eyebrow">
            <Link href="/">Projects</Link> / <Link href={`/projects/${t.project_id}`}>{t.project_key}</Link> / {t.key}
          </p>
          <h1>{t.title}</h1>
          <div className="row" style={{ marginTop: 8 }}>
            <span className="badge tone-info">{STAGE_LABEL[t.stage]}</span>
            <StatusBadge status={t.execution_status} />
            <PriorityBadge priority={t.priority} />
            <span className="small muted">updated <Elapsed since={t.updated_at} /> ago · v{t.version}</span>
          </div>
        </div>
      </div>

      <div className="split">
        <div>
          <div className="tabs" role="tablist" aria-label="Ticket sections">
            {TABS.map((name) => (
              <button key={name} role="tab" className="tab" aria-selected={tab === name}
                onClick={() => setTab(name)}>{name}</button>
            ))}
          </div>
          <div role="tabpanel" aria-label={tab}>
            {tab === "Overview" && <Overview view={data} />}
            {tab === "Requirements" && <Requirements view={data} onChange={refresh} />}
            {tab === "Audit" && <Audit projectId={t.project_id} taskId={t.id} />}
            {tab === "UX" && <UXTab view={data} />}
            {tab === "Code" && <CodeTab view={data} />}
            {tab === "QA" && <QATab taskId={t.id} />}
            {tab === "Runs" && <RunsTab taskId={t.id} />}
            {tab === "Costs" && <CostsTab taskId={t.id} />}
          </div>
        </div>
        <aside className="card pad" aria-label="Next actions">
          <h3 style={{ marginBottom: 12 }}>Next actions</h3>
          <Actions view={data} onDone={refresh} />
        </aside>
      </div>
    </>
  );
}

function Overview({ view }: { view: TaskView }) {
  const t = view.task;
  return (
    <div className="card pad stack">
      <p style={{ margin: 0, whiteSpace: "pre-wrap" }}>{t.description || <span className="muted">No description.</span>}</p>
      <dl className="kv">
        <dt>Stage</dt><dd>{STAGE_LABEL[t.stage]}</dd>
        <dt>Execution</dt><dd>{t.execution_status.toLowerCase()}{t.status_reason ? ` — ${t.status_reason}` : ""}</dd>
        <dt>Spec</dt><dd>{view.requirements.current_version ? `v${view.requirements.current_version}${view.requirements.approved_spec_id ? " (approved)" : " (draft)"}` : "—"}</dd>
        <dt>Head / base</dt><dd className="mono">{t.head_sha?.slice(0, 10) ?? "—"} / {t.base_sha?.slice(0, 10) ?? "—"}</dd>
        <dt>Repair cycles</dt><dd>{t.repair_count} of {t.repair_limit}</dd>
        <dt>Dependencies</dt>
        <dd>{view.dependencies.length ? view.dependencies.map((d) => (
          <Link key={d.id} href={`/tasks/${d.id}`} className="mono" style={{ marginRight: 8 }}>{d.key} · {STAGE_LABEL[d.stage]}</Link>
        )) : "None"}</dd>
      </dl>
      <div>
        <h3 style={{ marginBottom: 8 }}>Decisions</h3>
        {view.approvals.length === 0 ? <p className="muted small">No gate decisions yet.</p> : (
          <ul className="list">
            {view.approvals.map((a) => (
              <li key={a.id} className="spread">
                <span><strong>{a.gate.toLowerCase()}</strong> · {a.decision.toLowerCase().replace("_", " ")}
                  {a.reason && <span className="muted"> — {a.reason}</span>}</span>
                <span className="mono muted" title={a.scope_hash}>{a.scope_hash.slice(0, 8)} · <Elapsed since={a.created_at} /></span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function Requirements({ view, onChange }: { view: TaskView; onChange: () => Promise<void> }) {
  const r = view.requirements;
  const spec = r.current;
  return (
    <div className="stack">
      {r.open_blocking_questions.length > 0 && (
        <div className="notice" role="status">
          <strong>{r.open_blocking_questions.length} blocking question(s) must be resolved before approval</strong>
          <ul>{r.open_blocking_questions.map((q) => <li key={q.id}><span className="mono">{q.id}</span> {q.text}</li>)}</ul>
        </div>
      )}
      {!spec ? <Empty title="No specification yet"><p>Start analysis, then the BA agent (or a manual import) produces a versioned draft.</p></Empty> : (
        <div className="card pad stack">
          <div className="spread">
            <h2>Specification v{r.current_version}</h2>
            <span className={`badge ${r.approved_spec_id ? "tone-ok" : "tone-neutral"}`}>{r.approved_spec_id ? "approved" : "draft"}</span>
          </div>
          <p style={{ margin: 0 }}>{spec.goal}</p>
          <h3>Stories</h3>
          <ul className="list">{spec.stories.map((s) => (
            <li key={s.id}><span className="mono muted">{s.id}</span> As {s.as_a}, I want {s.i_want}, so that {s.so_that}.</li>
          ))}</ul>
          <h3>Acceptance criteria</h3>
          <table className="small" style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead><tr style={{ textAlign: "left" }} className="muted"><th scope="col">ID</th><th scope="col">Criterion</th><th scope="col">Verification</th><th scope="col">Required</th></tr></thead>
            <tbody>{spec.acceptance_criteria.map((ac) => (
              <tr key={ac.id} style={{ borderTop: "1px solid var(--line)" }}>
                <td className="mono" style={{ padding: "6px 8px 6px 0" }}>{ac.id}</td><td>{ac.statement}</td>
                <td className="muted">{ac.verification}</td><td>{ac.mandatory ? "Mandatory" : "Optional"}</td>
              </tr>
            ))}</tbody>
          </table>
          {spec.business_rules.length > 0 && <>
            <h3>Business rules</h3>
            <ul className="list">{spec.business_rules.map((b) => <li key={b.id}><span className="mono muted">{b.id}</span> {b.statement}</li>)}</ul>
          </>}
          {spec.questions.length > 0 && <>
            <h3>Questions</h3>
            <ul className="list">{spec.questions.map((q) => (
              <li key={q.id}><span className="mono muted">{q.id}</span> {q.text} {q.blocking && <span className="badge tone-warn">blocking</span>}
                <div className="small muted">{q.resolution ? `Resolved: ${q.resolution}` : "Unresolved"}</div></li>
            ))}</ul>
          </>}
        </div>
      )}
      {r.versions.length > 0 && (
        <div className="card pad">
          <h3 style={{ marginBottom: 8 }}>Version history</h3>
          <ul className="list">{r.versions.slice().reverse().map((v) => (
            <li key={v.id} className="spread small">
              <span>v{v.version} · {v.source.toLowerCase().replace("_", " ")} by {v.created_by_kind.toLowerCase()}</span>
              <span className="mono muted">{v.content_hash.slice(0, 12)} · <Elapsed since={v.created_at} /></span>
            </li>
          ))}</ul>
        </div>
      )}
      {["BA_ANALYSIS", "REQUIREMENTS_APPROVAL"].includes(view.task.stage) && <AntigravityBrief taskId={view.task.id} />}
      <ImportDraft view={view} onDone={onChange} />
    </div>
  );
}

function ImportDraft({ view, onDone }: { view: TaskView; onDone: () => Promise<void> }) {
  const [text, setText] = useState("");
  const [error, setError] = useState<string[] | null>(null);
  const editable = !["NEW", "MERGING", "DONE", "CANCELLED"].includes(view.task.stage);
  if (!editable) return null;
  return (
    <details className="card pad">
      <summary><strong>Import BA specification (JSON)</strong> <span className="muted small">— manual handoff from Gemini / Antigravity</span></summary>
      <form className="stack" style={{ marginTop: 12 }} onSubmit={async (e) => {
        e.preventDefault();
        let payload: unknown;
        try { payload = JSON.parse(text); } catch { return setError(["Not valid JSON."]); }
        try {
          await api.post(`/tasks/${view.task.id}/requirements`, {
            expected_version: view.task.version, payload, source: "MANUAL_IMPORT",
            provenance: { imported_via: "control-web", imported_at: new Date().toISOString() },
          });
          setText(""); setError(null); await onDone();
        } catch (err) {
          if (err instanceof ApiError) setError([err.body.message, ...Object.entries(err.body.field_errors).map(([k, v]) => `${k}: ${v}`)]);
        }
      }}>
        {view.requirements.approved_spec_id && (
          <p className="notice small">Importing a new version revokes the current approval and returns the ticket to analysis.</p>
        )}
        <label className="sr-only" htmlFor="spec-json">Specification JSON</label>
        <textarea id="spec-json" className="mono" rows={10} value={text} onChange={(e) => setText(e.target.value)}
          placeholder='{"goal": "...", "stories": [...], "acceptance_criteria": [...], "questions": [...]}' />
        {error && <div className="notice small" role="alert"><ul>{error.map((m) => <li key={m}>{m}</li>)}</ul></div>}
        <div><button className="btn btn-primary">Validate and import</button></div>
      </form>
    </details>
  );
}

function Audit({ projectId, taskId }: { projectId: string; taskId: string }) {
  const { data, error, loading, refresh } = useResource<{ items: AuditItem[] }>(
    `/projects/${projectId}/audit?object_id=${taskId}&limit=100`);
  if (loading) return <Loading />;
  if (error) return <ErrorState error={error} onRetry={refresh} />;
  if (!data?.items.length) return <Empty title="No audit events" />;
  return (
    <div className="card pad">
      <ul className="list">{data.items.map((e) => (
        <li key={e.event_id} className="spread small">
          <span>
            <span className={`badge ${e.outcome === "DENIED" ? "tone-danger" : "tone-neutral"}`}>{e.outcome.toLowerCase()}</span>{" "}
            <span className="mono">{e.action}</span> <span className="muted">by {e.actor_kind.toLowerCase()}</span>
            {e.reason && <div className="muted">{e.reason}</div>}
          </span>
          <span className="muted"><Elapsed since={e.created_at} /></span>
        </li>
      ))}</ul>
    </div>
  );
}

// ---------------------------------------------------------------- actions

const LABELS: Record<string, string> = {
  analyze: "Start analysis", approve_requirements: "Approve requirements", request_changes: "Request changes",
  approve_merge: "Approve merge", cancel: "Cancel ticket", resume: "Resume", retry: "Retry",
};
const NEEDS_REASON = new Set(["request_changes", "cancel", "resume", "retry"]);

function Actions({ view, onDone }: { view: TaskView; onDone: () => Promise<void> }) {
  const [active, setActive] = useState<PermittedAction | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const authorized = view.permitted_actions.filter((a) => a.authorized);
  // Hide actions this role can never take; explain the ones blocked only by the ticket's state.
  const relevant = authorized.filter((a) => a.allowed || !a.reasons.some((r) => r.includes("not permitted from") || r.includes("is terminal") || r.startsWith("execution status")));

  return (
    <>
      {authorized.length === 0 ? <p className="muted small">You have read-only access to this project.</p>
        : relevant.length === 0 && <p className="muted small">No actions are available in this stage.</p>}
      {relevant.map((a) => (
        <div key={a.command} className="action">
          <button className={`btn ${a.command.startsWith("approve") ? "btn-primary" : a.command === "cancel" ? "btn-danger" : ""}`}
            disabled={!a.allowed} aria-describedby={a.allowed ? undefined : `why-${a.command}`}
            onClick={() => { setActive(a); dialog.current?.showModal(); }}>
            {LABELS[a.command] ?? a.command}
          </button>
          {!a.allowed && <ul id={`why-${a.command}`} className="reasons">{a.reasons.map((r) => <li key={r}>{r}</li>)}</ul>}
        </div>
      ))}
      <dialog ref={dialog} onClose={() => setActive(null)} aria-labelledby="action-title">
        {active && <ActionForm view={view} action={active} onClose={() => dialog.current?.close()} onDone={onDone} />}
      </dialog>
    </>
  );
}

function ActionForm({ view, action, onClose, onDone }: {
  view: TaskView; action: PermittedAction; onClose: () => void; onDone: () => Promise<void>;
}) {
  const [reason, setReason] = useState("");
  const [repairs, setRepairs] = useState(1);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const t = view.task;
  const approvingReqs = action.command === "approve_requirements";
  const needsRepairs = action.command === "resume" && t.stage === "FIX_REQUIRED" && t.execution_status === "PAUSED";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      if (approvingReqs) {
        await api.post(`/tasks/${t.id}/approvals`, {
          gate: "REQUIREMENTS", decision: "APPROVED", expected_version: t.version,
          scope_hash: view.requirements.approval_scope_hash, reason: reason || null,
        });
      } else {
        await api.post(`/tasks/${t.id}/commands`, {
          command: action.command, expected_version: t.version, reason: reason || null,
          additional_repairs: needsRepairs ? repairs : null,
        });
      }
      onClose();
      await onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err : null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={submit}>
      <h2 id="action-title">{LABELS[action.command]} · {t.key}</h2>
      {approvingReqs && (
        <dl className="kv small">
          <dt>You are approving</dt><dd>Specification v{view.requirements.current_version}</dd>
          <dt>Criteria</dt><dd>{view.requirements.current?.acceptance_criteria.filter((a) => a.mandatory).length ?? 0} mandatory</dd>
          <dt>Scope hash</dt><dd className="mono" style={{ wordBreak: "break-all" }}>{view.requirements.approval_scope_hash}</dd>
        </dl>
      )}
      {approvingReqs && <p className="small muted" style={{ margin: 0 }}>Any later edit creates a new version and revokes this approval.</p>}
      {needsRepairs && (
        <label className="field">Additional repair cycles
          <select value={repairs} onChange={(e) => setRepairs(Number(e.target.value))}>{[1, 2, 3].map((n) => <option key={n}>{n}</option>)}</select>
        </label>
      )}
      <label className="field">{NEEDS_REASON.has(action.command) ? "Reason (required)" : "Note (optional)"}
        <textarea required={NEEDS_REASON.has(action.command)} maxLength={4000} value={reason} onChange={(e) => setReason(e.target.value)} />
      </label>
      {error && (
        <div className="notice small" role="alert">
          <strong>{error.body.message}</strong>
          {error.reasons.length > 0 && <ul>{error.reasons.map((r) => <li key={r}>{r}</li>)}</ul>}
        </div>
      )}
      <div className="row">
        <button className={`btn ${action.command === "cancel" ? "btn-danger" : "btn-primary"}`} disabled={busy}>Confirm</button>
        <button type="button" className="btn" onClick={onClose}>Close</button>
      </div>
    </form>
  );
}

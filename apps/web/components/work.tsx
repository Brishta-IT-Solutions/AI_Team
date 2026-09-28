"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { useResource } from "@/lib/useResource";
import type { CriterionResult, QAData, RunItem, TaskView } from "@/lib/types";
import { Elapsed } from "@/components/badges";
import { Empty, ErrorState, Loading } from "@/components/states";

const RUN_TONE: Record<RunItem["status"], string> = {
  QUEUED: "tone-neutral", RUNNING: "tone-info", SUCCEEDED: "tone-ok", FAILED: "tone-danger",
  BLOCKED: "tone-warn", CANCELLED: "tone-neutral",
};
const RESULT_TONE: Record<CriterionResult, string> = {
  PASS: "tone-ok", FAIL: "tone-danger", BLOCKED: "tone-warn", NOT_RUN: "tone-neutral",
};

function duration(run: RunItem): string {
  if (!run.started_at) return "waiting";
  const end = run.ended_at ? new Date(run.ended_at) : new Date();
  const s = Math.max(0, Math.round((end.getTime() - new Date(run.started_at).getTime()) / 1000));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}

function costLabel(run: RunItem): string {
  if (run.cost_quality === "SUBSCRIPTION") return "covered by plan";
  if (run.cost_quality === "LOCAL") return "local";
  if (run.cost === null) return "cost unknown";
  return `$${Number(run.cost).toFixed(2)} ${run.cost_quality === "PROVIDER_ESTIMATE" ? "(estimate)" : ""}`;
}

function useRuns(taskId: string) {
  return useResource<{ items: RunItem[] }>(`/tasks/${taskId}/runs`);
}

// ---------------------------------------------------------------- Runs (FR-12)

export function RunsTab({ taskId }: { taskId: string }) {
  const { data, error, loading, refresh } = useRuns(taskId);
  if (loading) return <Loading />;
  if (error) return <ErrorState error={error} onRetry={refresh} />;
  const parents = (data?.items ?? []).filter((r) => !r.parent_run_id);
  if (!parents.length) return <Empty title="No runs yet"><p>Start analysis and the team picks the ticket up.</p></Empty>;
  return (
    <div className="stack">
      {parents.map((run) => (
        <RunCard key={run.id} run={run} juniors={(data?.items ?? []).filter((c) => c.parent_run_id === run.id)} />
      ))}
    </div>
  );
}

function RunCard({ run, juniors }: { run: RunItem; juniors: RunItem[] }) {
  const [open, setOpen] = useState(run.status === "RUNNING");
  return (
    <div className="card pad">
      <div className="spread">
        <span><strong>{run.member}</strong> <span className="muted small">attempt {run.attempt}</span></span>
        <span className={`badge ${RUN_TONE[run.status]}`}>{run.status.toLowerCase()}</span>
      </div>
      <div className="small muted" style={{ marginTop: 4 }}>
        {run.milestone ?? (run.status === "QUEUED" ? `Waiting for ${run.member}` : "")}
      </div>
      <div className="row small" style={{ marginTop: 6 }}>
        <span>{duration(run)}</span><span className="muted">·</span>
        <span>{costLabel(run)}</span>
        {run.model && <><span className="muted">·</span><span className="mono">{run.model}</span></>}
      </div>
      {run.error?.message && <p className="notice small" style={{ marginTop: 8 }}>{run.error.message}</p>}
      {run.result?.review === "FAILED" && (
        <div className="notice small" style={{ marginTop: 8 }}>
          <strong>Self-check failed</strong>
          <ul>{run.result.findings?.map((f) => <li key={f}>{f}</li>)}</ul>
        </div>
      )}
      {run.result?.gate && run.result.gate !== "PASS" && run.result.reasons?.length ? (
        <p className="small muted" style={{ marginTop: 8 }}>QA: {run.result.reasons.join("; ")}</p>
      ) : null}
      {juniors.length > 0 && (
        <ul className="list small" style={{ marginTop: 8 }}>
          {juniors.map((c) => (
            <li key={c.id} className="spread">
              <span>↳ {c.member} junior task {c.result?.files_touched?.length ? `(${c.result.files_touched.join(", ")})` : ""}</span>
              <span className={`badge ${c.result?.accepted ? "tone-ok" : "tone-warn"}`}>
                {c.result?.accepted ? "patch accepted for review" : c.status === "SUCCEEDED" ? "patch rejected" : c.status.toLowerCase()}
              </span>
            </li>
          ))}
        </ul>
      )}
      {run.logs && run.logs.length > 0 && (
        <details open={open} onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)} style={{ marginTop: 8 }}>
          <summary className="small">Activity ({run.logs.length})</summary>
          <pre className="log">{run.logs.map((l) => `${l.type === "milestone" ? "▸ " : "  "}${l.message}`).join("\n")}</pre>
        </details>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- Code (FR-11)

export function CodeTab({ view }: { view: TaskView }) {
  const { data, loading } = useRuns(view.task.id);
  if (loading) return <Loading />;
  const dev = (data?.items ?? []).find((r) => r.role === "DEVELOPER" && r.result?.submission);
  const t = view.task;
  if (!dev) return <Empty title="No code yet"><p>Claude Code starts once the requirements are approved.</p></Empty>;
  const sub = dev.result!.submission!;
  return (
    <div className="stack">
      <div className="card pad">
        <dl className="kv">
          <dt>Branch</dt><dd className="mono">{t.branch ?? "—"}</dd>
          <dt>Head</dt><dd className="mono">{sub.head_sha.slice(0, 12)}</dd>
          <dt>Base</dt><dd className="mono">{sub.base_sha.slice(0, 12)}</dd>
          <dt>Self-check</dt>
          <dd>{dev.result?.review === "PASSED" ? <span className="badge tone-ok">passed</span> : <span className="badge tone-danger">failed</span>}</dd>
        </dl>
      </div>
      <div className="card pad">
        <h3 style={{ marginBottom: 8 }}>Files changed ({sub.files_changed.length})</h3>
        <ul className="list mono">{sub.files_changed.map((f) => <li key={f}>{f}</li>)}</ul>
      </div>
      {dev.result?.checks?.map((c) => (
        <details key={c.command_id} className="card pad">
          <summary><span className="mono">{c.command_id}</span> — exit {c.exit_code}{" "}
            <span className={`badge ${c.exit_code === 0 ? "tone-ok" : "tone-danger"}`}>{c.exit_code === 0 ? "pass" : "fail"}</span></summary>
          <pre className="log">{c.tail}</pre>
        </details>
      ))}
      {dev.result?.junior && dev.result.junior.length > 0 && (
        <div className="card pad">
          <h3 style={{ marginBottom: 8 }}>Delegated to the junior (Ollama)</h3>
          <ul className="list small">{dev.result.junior.map((j, i) => (
            <li key={i}>{j.task_type?.toLowerCase().replace("_", " ") ?? "task"} — {j.accepted ? "patch sent to Claude for review" : `refused: ${j.reasons?.join("; ")}`}</li>
          ))}</ul>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- UX

export function UXTab({ view }: { view: TaskView }) {
  const { data } = useRuns(view.task.id);
  const flows = view.requirements.current?.ux_flows ?? [];
  const dev = (data?.items ?? []).find((r) => r.role === "DEVELOPER" && r.result?.submission);
  if (!flows.length && !dev) return <Empty title="No UX work yet" />;
  return (
    <div className="stack">
      {flows.length > 0 && (
        <div className="card pad">
          <h3 style={{ marginBottom: 8 }}>Flows from the specification</h3>
          <ul className="list">{flows.map((f) => (
            <li key={f.id}><span className="mono muted">{f.id}</span> <strong>{f.name}</strong>
              <ol className="small" style={{ margin: "6px 0 0" }}>{f.steps.map((s) => <li key={s}>{s}</li>)}</ol></li>
          ))}</ul>
        </div>
      )}
      {dev && (
        <div className="card pad">
          <h3 style={{ marginBottom: 8 }}>Claude Code’s summary and UX notes</h3>
          <p style={{ whiteSpace: "pre-wrap", margin: 0 }}>{dev.result!.submission!.summary}</p>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- QA (FR-11)

export function QATab({ taskId }: { taskId: string }) {
  const { data, error, loading, refresh } = useResource<QAData>(`/tasks/${taskId}/qa`);
  if (loading) return <Loading />;
  if (error) return <ErrorState error={error} onRetry={refresh} />;
  const latest = data?.reports[0];
  if (!latest) return <Empty title="No QA report yet"><p>Codex tests every acceptance criterion once Claude’s self-check passes.</p></Empty>;
  return (
    <div className="stack">
      <div className="card pad">
        <div className="spread">
          <h2>Latest report</h2>
          <span className="row">
            <span className={`badge ${latest.verdict === "PASS" ? "tone-ok" : latest.verdict === "FAIL" ? "tone-danger" : "tone-warn"}`}>{latest.verdict.toLowerCase()}</span>
            <span className={`badge ${latest.current ? "tone-ok" : "tone-warn"}`}>{latest.current ? "current commit" : "stale"}</span>
          </span>
        </div>
        <p className="small muted mono">tested {latest.head_sha.slice(0, 12)} on {latest.base_sha.slice(0, 12)} · <Elapsed since={latest.created_at} /> ago</p>
        <table className="small" style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead><tr className="muted" style={{ textAlign: "left" }}><th scope="col">Criterion</th><th scope="col">Result</th><th scope="col">Evidence</th></tr></thead>
          <tbody>
            {latest.payload.criteria_results.map((c) => (
              <tr key={c.ac_id} style={{ borderTop: "1px solid var(--line)" }}>
                <td className="mono" style={{ padding: "6px 0" }}>{c.ac_id}</td>
                <td><span className={`badge ${RESULT_TONE[c.result]}`}>{c.result.replace("_", " ").toLowerCase()}</span></td>
                <td className="muted">{c.evidence_refs.length} file(s)</td>
              </tr>
            ))}
            {latest.payload.suites.filter((s) => s.mandatory).map((s) => (
              <tr key={s.name} style={{ borderTop: "1px solid var(--line)" }}>
                <td className="mono" style={{ padding: "6px 0" }}>{s.name}</td>
                <td><span className={`badge ${RESULT_TONE[s.result]}`}>{s.result.replace("_", " ").toLowerCase()}</span></td>
                <td className="muted">required check</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data!.defects.length > 0 && (
        <div className="card pad">
          <h3 style={{ marginBottom: 8 }}>Defects</h3>
          <ul className="list">{data!.defects.map((d) => (
            <li key={d.id}>
              <div className="spread">
                <span><span className="badge tone-danger">{d.severity.toLowerCase()}</span> {d.title}</span>
                <span className={`badge ${d.status === "OPEN" ? "tone-warn" : "tone-ok"}`}>{d.status.toLowerCase()}</span>
              </div>
              {d.evidence.expected && <div className="small muted">Expected: {d.evidence.expected} · Actual: {d.evidence.actual}</div>}
            </li>
          ))}</ul>
        </div>
      )}
      {data!.reports.length > 1 && (
        <p className="small muted">{data!.reports.length} reports in total; every retest is kept.</p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- Costs (FR-13)

export function CostsTab({ taskId }: { taskId: string }) {
  const { data, loading } = useRuns(taskId);
  if (loading) return <Loading />;
  const runs = data?.items ?? [];
  if (!runs.length) return <Empty title="No usage yet" />;
  const api$ = runs.filter((r) => r.cost !== null && !["SUBSCRIPTION", "LOCAL"].includes(r.cost_quality))
    .reduce((s, r) => s + Number(r.cost), 0);
  const count = (q: string) => runs.filter((r) => r.cost_quality === q).length;
  const unknown = runs.filter((r) => r.cost === null && r.status !== "QUEUED").length;
  return (
    <div className="card pad">
      <dl className="kv">
        <dt>API spend</dt><dd>${api$.toFixed(2)} <span className="muted small">(provider estimates)</span></dd>
        <dt>Covered by plans</dt><dd>{count("SUBSCRIPTION")} run(s)</dd>
        <dt>Local (Ollama)</dt><dd>{count("LOCAL")} run(s) — hardware cost not included</dd>
        <dt>Unknown</dt><dd>{unknown} run(s) <span className="muted small">— shown as unknown, never as zero</span></dd>
      </dl>
    </div>
  );
}

// ---------------------------------------------------------------- Antigravity brief

export function AntigravityBrief({ taskId }: { taskId: string }) {
  const [text, setText] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  return (
    <div className="card pad stack">
      <div className="spread">
        <span><strong>Work on this in Antigravity</strong><br />
          <span className="small muted">Copy the BA brief, paste it into Antigravity or Gemini, then import the JSON it returns below.</span></span>
        <button className="btn" onClick={async () => {
          const res = await api.get<{ markdown: string }>(`/tasks/${taskId}/ba-brief`);
          setText(res.markdown);
          try { await navigator.clipboard.writeText(res.markdown); setCopied(true); } catch { setCopied(false); }
        }}>Copy brief for Antigravity</button>
      </div>
      {text && (
        <>
          <p className="small muted" role="status">{copied ? "Copied to your clipboard." : "Select and copy the brief below."}</p>
          <textarea className="mono" rows={8} readOnly value={text} aria-label="BA brief" />
        </>
      )}
    </div>
  );
}

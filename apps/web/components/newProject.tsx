"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { ApiError, api } from "@/lib/api";
import type { Project } from "@/lib/types";

// Who else joins the new project, with the same roles they hold in the demo workspace.
const PEOPLE = [
  { subject: "product", label: "Pat Product", role: "PRODUCT_LEAD", roleLabel: "Product Lead" },
  { subject: "eng", label: "Eli Engineer", role: "ENGINEERING_LEAD", roleLabel: "Engineering Lead" },
  { subject: "qa", label: "Quinn QA", role: "QA_REVIEWER", roleLabel: "QA Reviewer" },
  { subject: "observer", label: "Olive Observer", role: "OBSERVER", roleLabel: "Observer" },
];

function keyFrom(name: string): string {
  const letters = name.toUpperCase().replace(/[^A-Z0-9]/g, "");
  return (/^[A-Z]/.test(letters) ? letters : `P${letters}`).slice(0, 10);
}

/** One step: project, repository, team, budgets and test command, then active (FR-04). */
export function NewProject({ onCancel }: { onCancel: () => void }) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [key, setKey] = useState("");
  const [keyEdited, setKeyEdited] = useState(false);
  const [repoUrl, setRepoUrl] = useState("");
  const [branch, setBranch] = useState("main");
  const [testCommand, setTestCommand] = useState("npm test");
  const [description, setDescription] = useState("");
  const [people, setPeople] = useState<string[]>(PEOPLE.map((p) => p.subject));
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const shownKey = keyEdited ? key : keyFrom(name);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const p = await api.post<Project>("/projects/setup", {
        key: shownKey, name, description, repo_url: repoUrl, base_branch: branch, test_command: testCommand,
        members: PEOPLE.filter((m) => people.includes(m.subject)).map((m) => ({ subject: m.subject, roles: [m.role] })),
      });
      router.push(`/projects/${p.id}?created=1`);
    } catch (err) {
      setError(err instanceof ApiError ? err : null);
      setBusy(false);
    }
  }

  const fieldError = (f: string) => error?.body.field_errors[f] ?? error?.body.field_errors[`body.${f}`];

  return (
    <form className="card pad stack" style={{ maxWidth: 640, marginBottom: 24 }} onSubmit={submit}>
      <div>
        <h2>New project</h2>
        <p className="small muted" style={{ margin: "4px 0 0" }}>
          The AI team works on the project’s own GitHub repository, on <span className="mono">feature/*</span> branches only.
          You review and merge them yourself.
        </p>
      </div>
      <div className="row" style={{ alignItems: "flex-start" }}>
        <label className="field" style={{ flex: 1 }}>Name
          <input required maxLength={160} value={name} onChange={(e) => setName(e.target.value)} autoFocus placeholder="Tasdeeq" />
        </label>
        <label className="field" style={{ width: 150 }}>Key
          <input required className="mono" pattern="[A-Z][A-Z0-9]{1,9}" title="2–10 capital letters or digits, starting with a letter"
            value={shownKey} onChange={(e) => { setKeyEdited(true); setKey(e.target.value.toUpperCase()); }} />
        </label>
      </div>
      <label className="field">GitHub repository
        <input required value={repoUrl} onChange={(e) => setRepoUrl(e.target.value)} placeholder="https://github.com/your-org/tasdeeq"
          aria-invalid={Boolean(fieldError("repo_url"))} />
        <span className="small muted">Private repositories need <span className="mono">GITHUB_TOKEN</span> in <span className="mono">.env</span>.</span>
      </label>
      <div className="row" style={{ alignItems: "flex-start" }}>
        <label className="field" style={{ width: 180 }}>Branch
          <input required className="mono" value={branch} onChange={(e) => setBranch(e.target.value)} aria-invalid={Boolean(fieldError("base_branch"))} />
        </label>
        <label className="field" style={{ flex: 1 }}>Test command
          <input required className="mono" value={testCommand} onChange={(e) => setTestCommand(e.target.value)} />
          <span className="small muted">Claude runs it before handing over; Codex runs it again to verify.</span>
        </label>
      </div>
      <label className="field">Description (optional)
        <textarea maxLength={20000} value={description} onChange={(e) => setDescription(e.target.value)} style={{ minHeight: 60 }} />
      </label>
      <fieldset className="stack" style={{ border: "none", padding: 0, margin: 0, gap: 6 }}>
        <legend className="small" style={{ marginBottom: 6 }}>People</legend>
        {PEOPLE.map((m) => (
          <label key={m.subject} className="row small">
            <input type="checkbox" checked={people.includes(m.subject)}
              onChange={(e) => setPeople(e.target.checked ? [...people, m.subject] : people.filter((s) => s !== m.subject))} />
            {m.label} <span className="muted">— {m.roleLabel}</span>
          </label>
        ))}
        <span className="small muted">You become its Administrator and Engineering Lead.</span>
      </fieldset>
      <p className="notice small" style={{ margin: 0 }}>
        <strong>Local pilot.</strong> GitHub branch protection isn’t checked yet, so those checks are marked waived, not passed.
      </p>
      {error && (
        <div className="notice small" role="alert">
          <strong>{error.body.message}</strong>
          {error.reasons.length > 0 && <ul>{error.reasons.map((r) => <li key={r}>{r}</li>)}</ul>}
        </div>
      )}
      <div className="row">
        <button className="btn btn-primary" disabled={busy}>{busy ? "Checking the repository…" : "Create project"}</button>
        <button type="button" className="btn" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}

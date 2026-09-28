"use client";

import Link from "next/link";
import { useState } from "react";
import { useResource } from "@/lib/useResource";
import type { Project } from "@/lib/types";
import { NewProject } from "@/components/newProject";
import { Empty, ErrorState, Loading } from "@/components/states";

const STATUS_TONE: Record<Project["status"], string> = {
  ACTIVE: "tone-ok", DRAFT: "tone-neutral", DISABLED: "tone-warn", ARCHIVED: "tone-neutral",
};

export default function Dashboard() {
  const { data, error, loading, refresh } = useResource<{ items: Project[] }>("/projects");
  const me = useResource<{ workspace_admin: boolean }>("/me");
  const [creating, setCreating] = useState(false);

  return (
    <>
      <div className="page-head">
        <div>
          <p className="eyebrow">Workspace</p>
          <h1>Projects</h1>
        </div>
        {me.data?.workspace_admin ? (
          !creating && <button className="btn btn-primary" onClick={() => setCreating(true)}>New project</button>
        ) : (
          <span className="small muted">Switch <strong>Acting as</strong> to Administrator to add a project.</span>
        )}
      </div>
      {creating && <NewProject onCancel={() => setCreating(false)} />}
      {loading ? <Loading /> : error ? <ErrorState error={error} onRetry={refresh} /> :
        !data?.items.length ? (
          <Empty title="No projects yet">
            <p>An Administrator creates a project, links its repository and activates it once every readiness check passes.</p>
          </Empty>
        ) : (
          <div className="grid grid-3">
            {data.items.map((p) => (
              <Link key={p.id} href={`/projects/${p.id}`} className="card pad" style={{ textDecoration: "none" }}>
                <div className="spread">
                  <span className="mono muted">{p.key}</span>
                  <span className="row">
                    {p.local_pilot && <span className="badge tone-info">local pilot</span>}
                    <span className={`badge ${STATUS_TONE[p.status]}`}>{p.status.toLowerCase()}</span>
                  </span>
                </div>
                <h2 style={{ margin: "10px 0 4px" }}>{p.name}</h2>
                <p className="muted small" style={{ margin: 0 }}>
                  {p.classification.toLowerCase()} data · policy v{p.policy_version}
                </p>
              </Link>
            ))}
          </div>
        )}
    </>
  );
}

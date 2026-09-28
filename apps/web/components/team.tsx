"use client";

import { useResource } from "@/lib/useResource";
import type { TeamMember } from "@/lib/types";

const STATE: Record<TeamMember["state"], { label: string; tone: string }> = {
  working: { label: "working", tone: "tone-info" },
  online: { label: "ready", tone: "tone-ok" },
  offline: { label: "no worker", tone: "tone-neutral" },
  not_configured: { label: "not set up", tone: "tone-warn" },
};

/** The four AI team members, their readiness and why a member is unavailable (FR-12). */
export function TeamPanel({ projectId, projectKey }: { projectId: string; projectKey?: string }) {
  const { data } = useResource<{ members: TeamMember[] }>(`/projects/${projectId}/team`);
  if (!data) return null;
  const unserved = projectKey && data.members.every((m) => m.state === "offline");
  return (
    <>
      {unserved && (
        <div className="notice small" role="status" style={{ marginBottom: 12 }}>
          <strong>No worker is serving {projectKey}.</strong> Check that <span className="mono">{projectKey}</span> is
          listed in <span className="mono">AITC_PROJECTS</span> in <span className="mono">.env</span> (for
          example <span className="mono">AITC_PROJECTS=PORTAL,TASDEEQ</span>), then
          run <span className="mono">docker compose up -d worker</span>.
        </div>
      )}
      <section aria-label="AI team" className="team">
        {data.members.map((m) => (
          <div key={m.role} className="card member">
            <div className="spread">
              <strong>{m.member}</strong>
              <span className={`badge ${STATE[m.state].tone}`}>{STATE[m.state].label}</span>
            </div>
            <div className="small muted">{m.title}{m.also ? ` · ${m.also}` : ""}</div>
            <div className="small" style={{ marginTop: 6 }}>
              {m.state === "working" ? `${m.running} running` : m.queued ? `${m.queued} waiting` : "idle"}
              {m.live_model && <span className="muted"> · {m.live_model}</span>}
            </div>
            {m.detail && <div className="small muted" style={{ marginTop: 4 }}>{m.detail}</div>}
          </div>
        ))}
      </section>
    </>
  );
}

"""Orchestrator: dispatches agent runs and applies their results (FR-02, FR-17, FR-18, FR-24, FR-25).

The team:
    BA         Gemini (automated) or Antigravity (manual brief + import)
    DEVELOPER  Claude Code, which may delegate donkey work to...
    JUNIOR     Ollama (patches only, reviewed by Claude, never committed directly)
    QA         Codex, on a separate workspace, without the developer's reasoning

Workers are untrusted relays for agent output. Every result is re-validated here and
re-authorized as the agent role that produced it. Stage changes still go through
`tasks.apply_transition`, audited under the orchestrator's service identity.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select

from control_api import prompts
from control_api.contracts import (
    BASpecification,
    DeveloperSubmission,
    JuniorAssignment,
    JuniorResult,
    QAReport,
    check_qa_coverage,
)
from control_api.db.models import (
    AcceptanceCriterion,
    Budget,
    Defect,
    Project,
    QAReportRecord,
    Repository,
    RequirementVersion,
    Reservation,
    Run,
    RunEvent,
    Task,
    Worker,
    utcnow,
)
from control_api.domain.junior_routing import route_assignment, validate_patch
from control_api.domain.lifecycle import ExecutionStatus, Stage, Trigger
from control_api.domain.permissions import Action, AgentRole, Principal, PrincipalKind
from control_api.domain.review import QAGate, defect_signature, dev_review, evaluate_qa
from control_api.errors import Conflict, NotFound
from control_api.services import leases
from control_api.services.audit import audit
from control_api.services.context import Ctx
from control_api.services.outbox import emit
from control_api.services.redaction import redact, redact_text

ROLE_TIMEOUT_SECONDS = {"BA": 600, "DEVELOPER": 2700, "QA": 1800, "JUNIOR": 300}
RUN_LEASE_SECONDS = 90  # heartbeats every 15 s; a silent worker is fenced after this
QUEUED_BRANCH_LEASE_SECONDS = 24 * 3600
MAX_CONSECUTIVE_SELF_CHECK_FAILURES = 3
MAX_PATCH_BYTES = 200_000
MAX_LOG_LINES = 50
TEAM = {
    "BA": {"member": "Gemini", "also": "Antigravity (manual brief)", "title": "Business analyst"},
    "DEVELOPER": {"member": "Claude Code", "title": "Developer and UX"},
    "QA": {"member": "Codex", "title": "Independent QA"},
    "JUNIOR": {"member": "Ollama", "title": "Junior (donkey work)"},
}
ACTIVE = ("QUEUED", "RUNNING")


def orchestrator_ctx(ctx: Ctx, project_id: uuid.UUID) -> Ctx:
    principal = Principal(kind=PrincipalKind.SERVICE, id="service:orchestrator",
                          scoped_project_id=str(project_id))
    return Ctx(ctx.session, principal, ctx.correlation_id)


def agent_ctx(ctx: Ctx, run: Run) -> Ctx:
    principal = Principal(kind=PrincipalKind.AGENT, id=f"agent:{run.role}:{run.id}",
                          agent_role=AgentRole(run.role), scoped_project_id=str(run.project_id))
    return Ctx(ctx.session, principal, ctx.correlation_id)


def _set_status(ctx: Ctx, task: Task, project: Project, status: ExecutionStatus,
                reason: str | None, action: str) -> None:
    from control_api.services.tasks import snapshot

    before = snapshot(task)
    task.execution_status = status.value
    task.status_reason = redact_text(reason)[:2000] if reason else None
    task.version += 1
    task.updated_at = utcnow()
    audit(ctx.session, ctx.principal, action=action, object_type="task", object_id=task.id,
          project_id=project.id, correlation_id=ctx.correlation_id, reason=task.status_reason,
          before=before, after=snapshot(task), policy_version=project.policy_version)
    emit(ctx.session, project_id=project.id, aggregate_type="task", aggregate_id=task.id,
         aggregate_version=task.version, type="task.status_changed",
         payload={"execution_status": task.execution_status, "reason": task.status_reason})


def _run_event(ctx: Ctx, run: Run, project: Project, type_: str, **payload: Any) -> None:
    emit(ctx.session, project_id=project.id, aggregate_type="run", aggregate_id=run.id,
         aggregate_version=run.attempt, type=type_,
         payload={"run_id": str(run.id), "task_id": str(run.task_id), "role": run.role,
                  "status": run.status, **payload})


# ---------------------------------------------------------------- budgets (FR-25)


def _cap(session, project_id: uuid.UUID, scope: str) -> Decimal | None:
    return session.scalar(select(Budget.cap).where(Budget.project_id == project_id,
                                                   Budget.scope == scope))


def budget_check(ctx: Ctx, project: Project, task: Task) -> tuple[Decimal | None, list[str]]:
    """Return (amount to reserve, unmet reasons). Caller holds the project row lock."""
    s = ctx.session
    run_cap = _cap(s, project.id, "RUN")
    ticket_cap = _cap(s, project.id, "TICKET")
    month_cap = _cap(s, project.id, "PROJECT_MONTH")
    if run_cap is None or ticket_cap is None or month_cap is None:
        return None, ["run, ticket and monthly budgets must all be configured"]
    month_start = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    active = select(func.coalesce(func.sum(Reservation.amount), 0)).where(
        Reservation.project_id == project.id, Reservation.status == "ACTIVE")
    spent = select(func.coalesce(func.sum(Run.cost), 0)).where(Run.project_id == project.id)
    month_used = (s.scalar(active) or 0) + (s.scalar(spent.where(Run.created_at >= month_start)) or 0)
    ticket_used = (s.scalar(active.where(Reservation.task_id == task.id)) or 0) + (
        s.scalar(spent.where(Run.task_id == task.id)) or 0)
    unmet = []
    if month_used + run_cap > month_cap:
        unmet.append(f"monthly budget: {month_used:.2f} used or reserved of {month_cap:.2f} USD")
    if ticket_used + run_cap > ticket_cap:
        unmet.append(f"ticket budget: {ticket_used:.2f} used or reserved of {ticket_cap:.2f} USD")
    return run_cap, unmet


def _release_reservation(ctx: Ctx, run: Run) -> None:
    res = ctx.session.scalar(select(Reservation).where(Reservation.run_id == run.id))
    if res is not None and res.status == "ACTIVE":
        res.status = "RELEASED"


# ---------------------------------------------------------------- dispatch


def active_parent_run(ctx: Ctx, task: Task) -> Run | None:
    return ctx.session.scalar(select(Run).where(
        Run.task_id == task.id, Run.parent_run_id.is_(None), Run.status.in_(ACTIVE)))


def _approved_spec(ctx: Ctx, task: Task) -> RequirementVersion | None:
    return ctx.session.get(RequirementVersion, task.approved_spec_id) if task.approved_spec_id else None


def _open_defects(ctx: Ctx, task: Task) -> list[dict[str, Any]]:
    rows = ctx.session.scalars(select(Defect).where(Defect.task_id == task.id, Defect.status == "OPEN")
                               .order_by(Defect.created_at)).all()
    return [{"ac_id": d.ac_id, "severity": d.severity, "title": d.title, **d.evidence} for d in rows]


def _consecutive_self_check_failures(ctx: Ctx, task: Task) -> int:
    runs = ctx.session.scalars(select(Run).where(Run.task_id == task.id, Run.parent_run_id.is_(None),
                                                 Run.role.in_(["DEVELOPER", "QA"]))
                               .order_by(Run.created_at.desc()).limit(10)).all()
    count = 0
    for r in runs:
        if r.role == "DEVELOPER" and (r.result or {}).get("review") == "FAILED":
            count += 1
        else:
            break
    return count


def _base_envelope(task: Task, project: Project, role: str, run_id: uuid.UUID) -> dict[str, Any]:
    return {
        "schema_version": "1.0", "run_id": str(run_id), "task_id": str(task.id), "task_key": task.key,
        "project_key": project.key, "project_name": project.name, "role": role,
        "title": task.title, "description": task.description,
        "deadline_seconds": ROLE_TIMEOUT_SECONDS[role], "policy_version": project.policy_version,
        "commands": project.policy.get("commands", []),
    }


def _create_run(ctx: Ctx, task: Task, project: Project, role: str, envelope: dict[str, Any],
                reserve: Decimal | None) -> Run:
    attempt = (ctx.session.scalar(select(func.count()).select_from(Run).where(
        Run.task_id == task.id, Run.role == role, Run.parent_run_id.is_(None))) or 0) + 1
    run = Run(project_id=project.id, task_id=task.id, role=role, attempt=attempt, envelope={})
    ctx.session.add(run)
    ctx.session.flush()
    env = {**_base_envelope(task, project, role, run.id), **envelope}
    renderer = {"BA": prompts.ba_prompt, "DEVELOPER": prompts.developer_prompt, "QA": prompts.qa_prompt}[role]
    run.envelope = {**env, "prompt": renderer(env), "prompt_version": prompts.PROMPT_VERSION}
    if reserve is not None:
        ctx.session.add(Reservation(project_id=project.id, task_id=task.id, run_id=run.id,
                                    amount=reserve))
    audit(ctx.session, ctx.principal, action="run.queue", object_type="run", object_id=run.id,
          project_id=project.id, correlation_id=ctx.correlation_id,
          details={"role": role, "attempt": attempt, "task_id": str(task.id),
                   "reserved": str(reserve) if reserve is not None else None})
    _run_event(ctx, run, project, "run.queued")
    return run


def dispatch(ctx: Ctx, task: Task, project: Project, *, feedback: list[str] | None = None) -> Run | None:
    """Start whatever the ticket's stage needs next. Safe to call repeatedly."""
    octx = orchestrator_ctx(ctx, project.id)
    ctx.session.flush()  # finished runs must be persisted before a new active run is inserted
    stage = Stage(task.stage)
    if stage in (Stage.DONE, Stage.CANCELLED) or active_parent_run(octx, task) is not None:
        return None
    if ExecutionStatus(task.execution_status) is ExecutionStatus.PAUSED:
        return None
    # Serialize budget decisions per project so racing tickets can't overspend (AT-12).
    ctx.session.execute(select(Project.id).where(Project.id == project.id).with_for_update())

    if stage is Stage.BA_ANALYSIS:
        amount, unmet = budget_check(octx, project, task)
        if unmet:
            _set_status(octx, task, project, ExecutionStatus.BLOCKED, "; ".join(unmet), "task.blocked")
            return None
        previous = ctx.session.get(RequirementVersion, task.current_spec_id) if task.current_spec_id else None
        run = _create_run(octx, task, project, "BA", {
            "feedback": feedback or [],
            "previous_spec": previous.payload if previous else None,
        }, amount)
        _set_status(octx, task, project, ExecutionStatus.QUEUED, "waiting for the BA", "task.queued")
        return run

    if stage in (Stage.READY_FOR_DEV, Stage.FIX_REQUIRED, Stage.DEVELOPING):
        return _dispatch_development(octx, task, project, stage, feedback or [])

    if stage is Stage.QA:
        spec = _approved_spec(octx, task)
        if spec is None or not task.head_sha or not task.base_sha:
            _set_status(octx, task, project, ExecutionStatus.BLOCKED,
                        "QA needs an approved spec and a developer commit", "task.blocked")
            return None
        amount, unmet = budget_check(octx, project, task)
        if unmet:
            _set_status(octx, task, project, ExecutionStatus.BLOCKED, "; ".join(unmet), "task.blocked")
            return None
        acs = ctx.session.scalars(select(AcceptanceCriterion).where(
            AcceptanceCriterion.spec_id == spec.id)).all()
        run = _create_run(octx, task, project, "QA", {
            "spec": spec.payload, "spec_version": spec.version, "spec_hash": spec.content_hash,
            "approved_ac_ids": sorted(a.stable_key for a in acs),
            "mandatory_ac_ids": sorted(a.stable_key for a in acs if a.mandatory),
            "branch": task.branch, "head_sha": task.head_sha, "base_sha": task.base_sha,
        }, amount)
        _set_status(octx, task, project, ExecutionStatus.QUEUED, "waiting for QA", "task.queued")
        return run
    return None


def _dispatch_development(octx: Ctx, task: Task, project: Project, stage: Stage,
                          feedback: list[str]) -> Run | None:
    from control_api.services.tasks import apply_transition, gather_facts

    repo = octx.session.scalar(select(Repository).where(Repository.project_id == project.id))
    spec = _approved_spec(octx, task)
    unmet: list[str] = []
    if repo is None:
        unmet.append("no repository linked")
    if spec is None:
        unmet.append("specification is not approved")
    if stage is Stage.FIX_REQUIRED and _consecutive_self_check_failures(octx, task) >= \
            MAX_CONSECUTIVE_SELF_CHECK_FAILURES:
        _set_status(octx, task, project, ExecutionStatus.PAUSED,
                    f"{MAX_CONSECUTIVE_SELF_CHECK_FAILURES} self-check failures in a row; human diagnosis needed",
                    "task.paused")
        return None
    amount, budget_unmet = budget_check(octx, project, task)
    unmet += budget_unmet
    if unmet:
        _set_status(octx, task, project, ExecutionStatus.BLOCKED, "; ".join(unmet), "task.blocked")
        return None
    assert repo is not None and spec is not None

    branch = task.branch or f"feature/{task.key.lower()}/{task.id.hex[:8]}"
    try:
        grant = leases.acquire(octx.session, project_id=project.id, repository_id=repo.id,
                               task_id=task.id, branch=branch, holder=f"task:{task.id}",
                               ttl_seconds=QUEUED_BRANCH_LEASE_SECONDS)
    except Conflict as exc:
        _set_status(octx, task, project, ExecutionStatus.BLOCKED, exc.message, "task.blocked")
        return None
    task.branch = branch
    repair_cycle = stage is Stage.FIX_REQUIRED or bool(task.current_qa_report_id)
    if stage is not Stage.DEVELOPING:
        trigger = Trigger.START_REPAIR if stage is Stage.FIX_REQUIRED else Trigger.START_DEVELOPMENT
        facts = gather_facts(octx, task, project)
        from dataclasses import replace
        apply_transition(octx, task, project, trigger,
                         replace(facts, budget_reserved=True, lease_acquired=True),
                         details={"branch": branch, "fencing_token": grant.fencing_token})
    findings = [*feedback, *(f"[{d['severity']}] {d['title']}" for d in _open_defects(octx, task))]
    run = _create_run(octx, task, project, "DEVELOPER", {
        "spec": spec.payload, "spec_version": spec.version, "spec_hash": spec.content_hash,
        "branch": branch, "base_branch": repo.base_branch, "head_sha": task.head_sha,
        "lease_token": grant.fencing_token, "repair_cycle": repair_cycle,
        # Only repairs after independent QA count toward the three-cycle limit (PRD §3).
        "counts_toward_repair_limit": bool(task.current_qa_report_id),
        "repair_findings": findings, "defects": _open_defects(octx, task),
        "protected_paths": project.policy.get("protected_paths", []),
        "junior": {"max_files": 10, "max_lines": 500, "max_complexity": 2},
    }, amount)
    _set_status(octx, task, project, ExecutionStatus.QUEUED, "waiting for the developer", "task.queued")
    return run


def cancel_active_runs(ctx: Ctx, task: Task, project: Project, reason: str) -> int:
    runs = ctx.session.scalars(select(Run).where(Run.task_id == task.id, Run.status.in_(ACTIVE))
                               .with_for_update()).all()
    for run in runs:
        run.cancel_requested = True
        run.status = "CANCELLED"
        run.ended_at = utcnow()
        run.error = {"code": "cancelled", "message": reason}
        _release_reservation(ctx, run)
        audit(ctx.session, ctx.principal, action="run.cancel", object_type="run", object_id=run.id,
              project_id=project.id, correlation_id=ctx.correlation_id, reason=reason)
        _run_event(ctx, run, project, "run.cancelled")
    return len(runs)


# ---------------------------------------------------------------- worker protocol


def _worker_ctx_project(ctx: Ctx) -> uuid.UUID:
    pid = ctx.principal.scoped_project_id
    if ctx.principal.is_human or not pid:
        raise Conflict("workers must use a project-scoped service identity", code="bad_principal")
    ctx.require(Action.RUN_CLAIM, pid, object_type="worker")
    return uuid.UUID(pid)


def record_worker(ctx: Ctx, *, worker_id: str, capabilities: dict[str, Any], version: str | None) -> None:
    project_id = _worker_ctx_project(ctx)
    reap_expired(ctx, project_id)
    worker = ctx.session.get(Worker, worker_id)
    if worker is None:
        worker = Worker(id=worker_id, project_id=project_id)
        ctx.session.add(worker)
    worker.capabilities = redact(capabilities)
    worker.version = version
    worker.last_seen_at = utcnow()


def reap_expired(ctx: Ctx, project_id: uuid.UUID) -> int:
    """Fence runs whose worker stopped heartbeating (FR-24). Never assumes no side effects."""
    now = utcnow()
    stale = ctx.session.scalars(select(Run).where(
        Run.project_id == project_id, Run.status == "RUNNING", Run.lease_expires_at < now,
    ).with_for_update(skip_locked=True)).all()
    for run in stale:
        run.status = "FAILED"
        run.ended_at = now
        run.error = {"code": "worker_lost", "message": "worker stopped heartbeating; attempt fenced"}
        _release_reservation(ctx, run)
        project = ctx.session.get(Project, run.project_id)
        octx = orchestrator_ctx(ctx, run.project_id)
        if run.parent_run_id is None:
            task = ctx.session.get(Task, run.task_id)
            if run.role == "DEVELOPER":
                leases.release(ctx.session, task_id=task.id, reason="WORKER_LOST")
            _set_status(octx, task, project, ExecutionStatus.FAILED,
                        f"{TEAM[run.role]['member']} worker stopped responding; retry when it is back",
                        "task.failed")
        audit(ctx.session, octx.principal, action="run.fence", object_type="run", object_id=run.id,
              project_id=run.project_id, correlation_id=ctx.correlation_id, reason="worker lost")
        _run_event(octx, run, project, "run.failed", error="worker_lost")
    return len(stale)


def claim(ctx: Ctx, *, worker_id: str, roles: list[str]) -> dict[str, Any] | None:
    project_id = _worker_ctx_project(ctx)
    reap_expired(ctx, project_id)
    roles = [r for r in roles if r in ("BA", "DEVELOPER", "QA")]
    run = ctx.session.scalar(select(Run).where(
        Run.project_id == project_id, Run.status == "QUEUED", Run.parent_run_id.is_(None),
        Run.role.in_(roles),
    ).order_by(Run.created_at).limit(1).with_for_update(skip_locked=True))
    if run is None:
        return None
    task = ctx.session.scalar(select(Task).where(Task.id == run.task_id).with_for_update())
    project = ctx.session.get(Project, project_id)
    octx = orchestrator_ctx(ctx, project_id)
    if run.role == "DEVELOPER":
        repo = ctx.session.scalar(select(Repository).where(Repository.project_id == project_id))
        try:
            leases.heartbeat(ctx.session, branch=run.envelope["branch"], repository_id=repo.id,
                             token=run.envelope["lease_token"], ttl_seconds=RUN_LEASE_SECONDS)
        except Conflict:
            run.status, run.ended_at = "FAILED", utcnow()
            run.error = {"code": "lease_fenced", "message": "branch lease expired while queued"}
            _release_reservation(ctx, run)
            _set_status(octx, task, project, ExecutionStatus.FAILED,
                        "branch lease expired while queued; retry", "task.failed")
            return None
    run.status = "RUNNING"
    run.worker_id = worker_id
    run.claim_token = secrets.token_hex(24)
    run.started_at = run.heartbeat_at = utcnow()
    run.lease_expires_at = run.started_at + timedelta(seconds=RUN_LEASE_SECONDS)
    member = TEAM[run.role]["member"]
    _set_status(octx, task, project, ExecutionStatus.RUNNING, f"{member} is working", "task.running")
    audit(ctx.session, ctx.principal, action="run.claim", object_type="run", object_id=run.id,
          project_id=project_id, correlation_id=ctx.correlation_id, details={"worker_id": worker_id})
    _run_event(octx, run, project, "run.started", worker_id=worker_id)
    return {"run_id": str(run.id), "role": run.role, "attempt": run.attempt,
            "claim_token": run.claim_token, "envelope": run.envelope}


def _load_claimed(ctx: Ctx, run_id: uuid.UUID, claim_token: str, *, allow_cancelled: bool = False) -> Run:
    project_id = _worker_ctx_project(ctx)
    run = ctx.session.scalar(select(Run).where(Run.id == run_id).with_for_update())
    if run is None or run.project_id != project_id:
        raise NotFound("run not found")
    if not run.claim_token or not secrets.compare_digest(run.claim_token, claim_token):
        raise Conflict("stale or unknown claim token", code="claim_fenced")
    if run.status != "RUNNING" and not (allow_cancelled and run.status == "CANCELLED"):
        raise Conflict(f"run is {run.status}", code="claim_fenced")
    return run


def heartbeat(ctx: Ctx, run_id: uuid.UUID, *, claim_token: str, milestone: str | None,
              logs: list[dict[str, Any]]) -> dict[str, Any]:
    run = _load_claimed(ctx, run_id, claim_token, allow_cancelled=True)
    if run.status == "CANCELLED":
        return {"cancel": True}
    now = utcnow()
    run.heartbeat_at = now
    run.lease_expires_at = now + timedelta(seconds=RUN_LEASE_SECONDS)
    if milestone:
        run.milestone = redact_text(milestone)[:200]
    if run.role == "DEVELOPER" and run.parent_run_id is None:
        repo = ctx.session.scalar(select(Repository).where(Repository.project_id == run.project_id))
        leases.heartbeat(ctx.session, branch=run.envelope["branch"], repository_id=repo.id,
                         token=run.envelope["lease_token"], ttl_seconds=RUN_LEASE_SECONDS)
    last = ctx.session.scalar(select(func.max(RunEvent.sequence)).where(RunEvent.run_id == run.id)) or 0
    for i, line in enumerate(logs[:MAX_LOG_LINES], start=1):
        ctx.session.add(RunEvent(run_id=run.id, sequence=last + i, type=str(line.get("type", "log"))[:20],
                                 message=redact_text(str(line.get("message", "")))[:4000]))
    if milestone:
        project = ctx.session.get(Project, run.project_id)
        _run_event(orchestrator_ctx(ctx, run.project_id), run, project, "run.milestone",
                   milestone=run.milestone)
    return {"cancel": run.cancel_requested}


# ---------------------------------------------------------------- junior delegation (FR-21)


def request_junior(ctx: Ctx, parent_id: uuid.UUID, *, claim_token: str,
                   assignments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    parent = _load_claimed(ctx, parent_id, claim_token)
    if parent.role != "DEVELOPER":
        raise Conflict("only the developer may delegate junior work", code="illegal_delegation")
    project = ctx.session.get(Project, parent.project_id)
    extra = tuple(project.policy.get("protected_paths", []))
    out: list[dict[str, Any]] = []
    for raw in assignments[:10]:
        try:
            assignment = JuniorAssignment.model_validate({**raw, "parent_run_id": str(parent.id)})
        except ValidationError as exc:
            out.append({"accepted": False, "reasons": [e["msg"] for e in exc.errors()][:5]})
            continue
        decision = route_assignment(assignment, extra)
        if not decision.accepted:
            audit(ctx.session, ctx.principal, action="junior.route.reject", object_type="run",
                  object_id=parent.id, project_id=project.id, correlation_id=ctx.correlation_id,
                  outcome="DENIED", reason="; ".join(decision.reasons))
            out.append({"accepted": False, "reasons": list(decision.reasons)})
            continue
        child = Run(project_id=project.id, task_id=parent.task_id, parent_run_id=parent.id, role="JUNIOR",
                    status="RUNNING", worker_id=parent.worker_id, claim_token=secrets.token_hex(24),
                    envelope={"assignment": assignment.model_dump(mode="json"),
                              "deadline_seconds": ROLE_TIMEOUT_SECONDS["JUNIOR"]},
                    started_at=utcnow(), heartbeat_at=utcnow(),
                    lease_expires_at=utcnow() + timedelta(seconds=ROLE_TIMEOUT_SECONDS["JUNIOR"] + 60))
        ctx.session.add(child)
        ctx.session.flush()
        audit(ctx.session, ctx.principal, action="junior.route.accept", object_type="run",
              object_id=child.id, project_id=project.id, correlation_id=ctx.correlation_id,
              details={"parent_run_id": str(parent.id), "task_type": assignment.task_type})
        out.append({"accepted": True, "run_id": str(child.id), "claim_token": child.claim_token,
                    "assignment": child.envelope["assignment"]})
    return out


# ---------------------------------------------------------------- results


def _usage_cost(usage: dict[str, Any]) -> tuple[Decimal | None, str]:
    quality = str(usage.get("quality") or "UNKNOWN")
    if quality in ("LOCAL", "SUBSCRIPTION"):  # covered by hardware or a plan, not API spend
        return Decimal("0"), quality
    amount = usage.get("cost_usd")
    try:
        return (Decimal(str(amount)), quality) if amount is not None else (None, "UNKNOWN")
    except ArithmeticError:
        return None, "UNKNOWN"


def submit_result(ctx: Ctx, run_id: uuid.UUID, *, claim_token: str, outcome: str,
                  payload: dict[str, Any], usage: dict[str, Any], errors: list[dict[str, Any]],
                  provider: str | None, model: str | None) -> dict[str, Any]:
    run = _load_claimed(ctx, run_id, claim_token, allow_cancelled=True)
    project = ctx.session.get(Project, run.project_id)
    octx = orchestrator_ctx(ctx, run.project_id)
    run.usage = redact(usage)
    run.cost, run.cost_quality = _usage_cost(usage)
    run.provider, run.model = provider, model
    run.ended_at = utcnow()
    _release_reservation(ctx, run)

    if run.status == "CANCELLED":
        # Late result after cancellation: keep its cost, apply nothing (AT-16).
        audit(ctx.session, ctx.principal, action="run.result.after_cancel", object_type="run",
              object_id=run.id, project_id=project.id, correlation_id=ctx.correlation_id)
        return {"applied": False, "reason": "run was cancelled"}

    if outcome != "SUCCEEDED":
        run.status = "BLOCKED" if outcome == "BLOCKED" else "FAILED"
        run.error = redact(errors[0] if errors else {"code": "failed", "message": "run failed"})
        _run_event(octx, run, project, "run.failed", error=run.error.get("code"))
        if run.parent_run_id is None:
            task = ctx.session.scalar(select(Task).where(Task.id == run.task_id).with_for_update())
            status = ExecutionStatus.BLOCKED if outcome == "BLOCKED" else ExecutionStatus.FAILED
            _set_status(octx, task, project, status,
                        f"{TEAM[run.role]['member']}: {run.error.get('message', 'failed')}", "task.run_failed")
        return {"applied": False, "reason": run.error.get("message")}

    handler = {"BA": _apply_ba, "DEVELOPER": _apply_dev, "QA": _apply_qa, "JUNIOR": _apply_junior}[run.role]
    try:
        result = handler(ctx, octx, run, project, payload)
    except ValidationError as exc:
        return _reject(ctx, octx, run, project, "invalid_result",
                       "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:8]))
    run.status = "SUCCEEDED"
    _run_event(octx, run, project, "run.succeeded")
    return result


def _reject(ctx: Ctx, octx: Ctx, run: Run, project: Project, code: str, message: str) -> dict[str, Any]:
    run.status = "FAILED"
    run.error = {"code": code, "message": redact_text(message)[:4000]}
    _run_event(octx, run, project, "run.failed", error=code)
    if run.parent_run_id is None:
        task = ctx.session.scalar(select(Task).where(Task.id == run.task_id).with_for_update())
        status = ExecutionStatus.BLOCKED if run.role == "QA" else ExecutionStatus.FAILED
        _set_status(octx, task, project, status,
                    f"{TEAM[run.role]['member']} result rejected: {run.error['message']}", "task.result_rejected")
    audit(ctx.session, ctx.principal, action="run.result.reject", object_type="run", object_id=run.id,
          project_id=project.id, correlation_id=ctx.correlation_id, outcome="DENIED",
          reason=run.error["message"])
    return {"applied": False, "reason": run.error["message"]}


def _apply_ba(ctx: Ctx, octx: Ctx, run: Run, project: Project, payload: dict[str, Any]) -> dict[str, Any]:
    from control_api.services.tasks import submit_requirements

    spec = BASpecification.model_validate(payload)
    task = ctx.session.get(Task, run.task_id)
    if Stage(task.stage) is not Stage.BA_ANALYSIS:
        return _reject(ctx, octx, run, project, "stale", f"ticket moved on to {task.stage}")
    run.result = {"spec_goal": spec.goal[:200]}
    run.status = "SUCCEEDED"  # before submit_requirements, so it is not cancelled as obsolete
    _, version, _ = submit_requirements(agent_ctx(ctx, run), task.id, spec=spec,
                                        expected_version=task.version, source="AGENT",
                                        provenance={"run_id": str(run.id), "provider": run.provider,
                                                    "model": run.model})
    return {"applied": True, "spec_version": version.version}


def _apply_dev(ctx: Ctx, octx: Ctx, run: Run, project: Project, payload: dict[str, Any]) -> dict[str, Any]:
    from control_api.services.tasks import apply_transition, gather_facts

    submission = DeveloperSubmission.model_validate(payload.get("submission", payload))
    task = ctx.session.scalar(select(Task).where(Task.id == run.task_id).with_for_update())
    if Stage(task.stage) is not Stage.DEVELOPING:
        return _reject(ctx, octx, run, project, "stale", f"ticket moved on to {task.stage}")
    repo = ctx.session.scalar(select(Repository).where(Repository.project_id == project.id))
    try:  # a fenced worker can neither push nor finalize (AT-14)
        leases.assert_current(ctx.session, repository_id=repo.id, branch=run.envelope["branch"],
                              token=run.envelope["lease_token"])
    except Conflict as exc:
        return _reject(ctx, octx, run, project, "lease_fenced", exc.message)

    task.head_sha, task.base_sha = submission.head_sha, submission.base_sha
    # JSONB columns are replaced, never mutated in place, so every change is persisted.
    base_result = {"submission": submission.model_dump(mode="json"), "junior": redact(payload.get("junior", [])),
                   "checks": redact(payload.get("checks", []))[:10]}
    run.result = base_result
    apply_transition(octx, task, project, Trigger.SUBMIT_DEVELOPMENT, gather_facts(octx, task, project),
                     details={"head_sha": submission.head_sha, "run_id": str(run.id)})
    leases.release(ctx.session, task_id=task.id, reason="SUBMITTED")
    required = [c["id"] for c in project.policy.get("commands", []) if c.get("required", True)]
    findings = dev_review(submission, required_commands=required,
                          protected_globs=project.policy.get("protected_paths", []))
    facts = gather_facts(octx, task, project)
    from dataclasses import replace
    if findings:
        run.result = {**base_result, "review": "FAILED", "findings": findings}
        apply_transition(octx, task, project, Trigger.DEV_REVIEW_FAILED, facts,
                         reason="; ".join(findings)[:2000], details={"findings": findings})
        run.status = "SUCCEEDED"
        dispatch(octx, task, project, feedback=findings)
        return {"applied": True, "review": "FAILED", "findings": findings}
    run.result = {**base_result, "review": "PASSED"}
    if run.envelope.get("counts_toward_repair_limit"):
        task.repair_count += 1  # a candidate repaired after QA reached QA again (FR-18)
    apply_transition(octx, task, project, Trigger.DEV_REVIEW_PASSED,
                     replace(facts, scope_valid=True, self_checks_passed=True),
                     details={"head_sha": task.head_sha, "repair_count": task.repair_count})
    run.status = "SUCCEEDED"
    dispatch(octx, task, project)
    return {"applied": True, "review": "PASSED"}


def _apply_qa(ctx: Ctx, octx: Ctx, run: Run, project: Project, payload: dict[str, Any]) -> dict[str, Any]:
    from control_api.services.tasks import apply_transition, gather_facts

    report = QAReport.model_validate(payload)
    task = ctx.session.scalar(select(Task).where(Task.id == run.task_id).with_for_update())
    if Stage(task.stage) is not Stage.QA:
        return _reject(ctx, octx, run, project, "stale", f"ticket moved on to {task.stage}")
    spec = _approved_spec(ctx, task)
    approved = set(run.envelope["approved_ac_ids"])
    problems = check_qa_coverage(report, approved, spec_version=spec.version,
                                 head_sha=task.head_sha, base_sha=task.base_sha)
    if problems:  # AT-07: never MERGE_APPROVAL on an incomplete or stale report
        return _reject(ctx, octx, run, project, "qa_report_invalid", "; ".join(problems))

    outcome = evaluate_qa(report, set(run.envelope["mandatory_ac_ids"]))
    record = QAReportRecord(project_id=project.id, task_id=task.id, run_id=run.id, spec_id=spec.id,
                            head_sha=report.head_sha, base_sha=report.base_sha, verdict=outcome.gate.value,
                            payload=report.model_dump(mode="json"))
    ctx.session.add(record)
    ctx.session.flush()
    run.result = {"qa_report_id": str(record.id), "gate": outcome.gate.value, "reasons": list(outcome.reasons)}
    run.status = "SUCCEEDED"
    facts = gather_facts(octx, task, project)
    from dataclasses import replace

    if outcome.gate is QAGate.BLOCKED:
        _set_status(octx, task, project, ExecutionStatus.BLOCKED, "; ".join(outcome.reasons), "task.blocked")
        return {"applied": True, "gate": "BLOCKED"}

    task.current_qa_report_id = record.id
    if outcome.gate is QAGate.PASS:
        for d in ctx.session.scalars(select(Defect).where(Defect.task_id == task.id, Defect.status == "OPEN")):
            d.status, d.last_report_id, d.updated_at = "RESOLVED", record.id, utcnow()
        apply_transition(octx, task, project, Trigger.QA_PASSED,
                         replace(facts, qa_mandatory_all_pass=True, qa_evidence_current=True),
                         details={"qa_report_id": str(record.id), "head_sha": task.head_sha})
        return {"applied": True, "gate": "PASS"}

    _record_defects(ctx, task, project, report, record)
    apply_transition(octx, task, project, Trigger.QA_FAILED,
                     replace(facts, functional_failure_with_defects=True),
                     reason="; ".join(outcome.reasons), details={"qa_report_id": str(record.id)})
    dispatch(octx, task, project)
    return {"applied": True, "gate": "FAIL", "paused": task.execution_status == "PAUSED"}


def _record_defects(ctx: Ctx, task: Task, project: Project, report: QAReport,
                    record: QAReportRecord) -> None:
    items: list[tuple[str | None, str, str, dict[str, Any]]] = []
    for f in report.findings:
        items.append((f.ac_id, f.severity.value, f.title, {
            "reproduction_steps": f.reproduction_steps, "expected": f.expected, "actual": f.actual,
            "test_command": f.test_command, "tested_sha": report.head_sha}))
    covered = {f.ac_id for f in report.findings}
    for c in report.criteria_results:
        if c.result.value == "FAIL" and c.ac_id not in covered:
            items.append((c.ac_id, "HIGH", f"{c.ac_id} failed", {
                "evidence_refs": c.evidence_refs, "tested_sha": report.head_sha}))
    for ac_id, severity, title, evidence in items:
        signature = defect_signature(ac_id, title)
        defect = ctx.session.scalar(select(Defect).where(Defect.task_id == task.id,
                                                         Defect.signature == signature))
        if defect is None:
            ctx.session.add(Defect(project_id=project.id, task_id=task.id, signature=signature,
                                   ac_id=ac_id, severity=severity, title=title[:300],
                                   evidence=redact(evidence), first_report_id=record.id,
                                   last_report_id=record.id))
        else:
            defect.status, defect.severity, defect.last_report_id = "OPEN", severity, record.id
            defect.evidence, defect.updated_at = redact(evidence), utcnow()
    ctx.session.flush()


def _apply_junior(ctx: Ctx, octx: Ctx, run: Run, project: Project, payload: dict[str, Any]) -> dict[str, Any]:
    patch = str(payload.get("patch", ""))
    result = JuniorResult.model_validate({k: v for k, v in payload.items() if k != "patch"})
    reasons: list[str] = []
    if len(patch.encode()) > MAX_PATCH_BYTES:
        reasons.append("patch exceeds size limit")
    if hashlib.sha256(patch.encode()).hexdigest() != result.patch_ref:
        reasons.append("patch_ref does not match the patch content")
    assignment = JuniorAssignment.model_validate(run.envelope["assignment"])
    decision = validate_patch(assignment, result,
                              extra_protected_globs=tuple(project.policy.get("protected_paths", [])))
    reasons += list(decision.reasons)
    accepted = not reasons
    run.result = {"accepted": accepted, "reasons": reasons, "files_touched": result.files_touched,
                  "lines_changed": result.lines_changed, "patch_ref": result.patch_ref}
    audit(ctx.session, ctx.principal, action="junior.patch." + ("accept" if accepted else "reject"),
          object_type="run", object_id=run.id, project_id=project.id, correlation_id=ctx.correlation_id,
          outcome="ALLOWED" if accepted else "DENIED", reason="; ".join(reasons) or None)
    # The patch goes back to the developer for review; it is never committed from here.
    return {"applied": True, "accepted": accepted, "reasons": reasons}


# ---------------------------------------------------------------- read models


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value else None


def run_view(ctx: Ctx, run: Run, *, with_logs: bool) -> dict[str, Any]:
    out = {
        "id": str(run.id), "role": run.role, "member": TEAM[run.role]["member"], "status": run.status,
        "attempt": run.attempt, "parent_run_id": str(run.parent_run_id) if run.parent_run_id else None,
        "milestone": run.milestone, "provider": run.provider, "model": run.model, "worker_id": run.worker_id,
        "usage": run.usage, "cost": str(run.cost) if run.cost is not None else None,
        "cost_quality": run.cost_quality, "error": run.error, "result": run.result,
        "created_at": _iso(run.created_at), "started_at": _iso(run.started_at),
        "ended_at": _iso(run.ended_at), "heartbeat_at": _iso(run.heartbeat_at),
    }
    if with_logs:
        events = ctx.session.scalars(select(RunEvent).where(RunEvent.run_id == run.id)
                                     .order_by(RunEvent.sequence.desc()).limit(200)).all()
        out["logs"] = [{"sequence": e.sequence, "type": e.type, "message": e.message,
                        "at": _iso(e.created_at)} for e in reversed(events)]
    return out


def team_view(ctx: Ctx, project: Project) -> list[dict[str, Any]]:
    from control_api.db.models import AgentConfig

    cutoff = utcnow() - timedelta(seconds=RUN_LEASE_SECONDS)
    workers = ctx.session.scalars(select(Worker).where(Worker.project_id == project.id,
                                                       Worker.last_seen_at >= cutoff)).all()
    out = []
    for role, info in TEAM.items():
        config = ctx.session.scalar(select(AgentConfig).where(
            AgentConfig.project_id == project.id, AgentConfig.role == role)
            .order_by(AgentConfig.version.desc()).limit(1))
        live = [w.capabilities.get(role) for w in workers if (w.capabilities.get(role) or {}).get("available")]
        offline_detail = next((w.capabilities.get(role, {}).get("detail") for w in workers
                               if role in w.capabilities), None)
        busy = ctx.session.scalar(select(func.count()).select_from(Run).where(
            Run.project_id == project.id, Run.role == role, Run.status == "RUNNING")) or 0
        queued = ctx.session.scalar(select(func.count()).select_from(Run).where(
            Run.project_id == project.id, Run.role == role, Run.status == "QUEUED")) or 0
        out.append({
            "role": role, **info,
            "configured_model": config.model if config else None,
            "state": "working" if busy else ("online" if live else ("not_configured" if workers else "offline")),
            "live_model": (live[0] or {}).get("model") if live else None,
            "detail": offline_detail if not live else None,
            "running": busy, "queued": queued,
        })
    return out


def ba_brief(ctx: Ctx, task: Task, project: Project) -> str:
    previous = ctx.session.get(RequirementVersion, task.current_spec_id) if task.current_spec_id else None
    env = {**_base_envelope(task, project, "BA", uuid.uuid4()),
           "previous_spec": previous.payload if previous else None, "feedback": []}
    return prompts.ba_brief(env)

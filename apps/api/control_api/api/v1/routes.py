from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import ValidationError
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from control_api.api.v1 import schemas
from control_api.api.v1.common import decode_cursor, encode_cursor, make_ctx, mutate
from control_api.auth import current_principal
from control_api.config import get_settings
from control_api.contracts import BASpecification
from control_api.db.models import AuditEvent, Defect, OutboxEvent, Project, QAReportRecord, Run, Task
from control_api.db.session import get_session
from control_api.domain.lifecycle import KANBAN_COLUMN_FOR_STAGE, KANBAN_COLUMNS
from control_api.domain.permissions import Action, Principal
from control_api.errors import Unprocessable
from control_api.services import orchestrator
from control_api.services import projects as project_svc
from control_api.services import tasks as task_svc
from control_api.services.outbox import envelope

router = APIRouter(prefix="/v1")


def _page_limit(limit: int | None) -> int:
    s = get_settings()
    return max(1, min(limit or s.page_size_default, s.page_size_max))


def _project_json(p: Project) -> dict[str, Any]:
    return {**project_svc.project_snapshot(p), "description": p.description,
            "created_at": p.created_at.isoformat()}


def _task_card(t: Task) -> dict[str, Any]:
    return {
        "id": str(t.id), "key": t.key, "title": t.title, "priority": t.priority,
        "stage": t.stage, "execution_status": t.execution_status, "version": t.version,
        "column": KANBAN_COLUMN_FOR_STAGE[t.stage], "status_reason": t.status_reason,
        "repair_count": t.repair_count, "created_at": t.created_at.isoformat(),
        "updated_at": t.updated_at.isoformat(),
    }


# ---------------------------------------------------------------- identity


@router.get("/me")
def me(principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    return {"id": principal.id, "kind": principal.kind, "workspace_admin": principal.workspace_admin,
            "projects": {pid: sorted(r.value for r in roles)
                         for pid, roles in principal.project_roles.items()}}


# ---------------------------------------------------------------- projects


@router.get("/projects")
def list_projects(request: Request, session: Session = Depends(get_session),
                  principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    ctx = make_ctx(request, session, principal)
    return {"items": [_project_json(p) for p in project_svc.list_projects(ctx)]}


@router.post("/projects", status_code=201)
def create_project(body: schemas.ProjectCreate, request: Request,
                   session: Session = Depends(get_session),
                   principal: Principal = Depends(current_principal)):
    ctx = make_ctx(request, session, principal)

    def run():
        p = project_svc.create_project(ctx, key=body.key, name=body.name,
                                       description=body.description,
                                       classification=body.classification)
        return 201, _project_json(p)
    return mutate(request, session, principal, body.model_dump(mode="json"), run)


@router.get("/projects/{project_id}")
def get_project(project_id: uuid.UUID, request: Request, session: Session = Depends(get_session),
                principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    ctx = make_ctx(request, session, principal)
    return _project_json(project_svc.get_project(ctx, project_id))


@router.put("/projects/{project_id}/members")
def set_member(project_id: uuid.UUID, body: schemas.MembershipSet, request: Request,
               session: Session = Depends(get_session),
               principal: Principal = Depends(current_principal)):
    ctx = make_ctx(request, session, principal)

    def run():
        m = project_svc.set_membership(ctx, project_id, subject=body.subject, roles=body.roles)
        return 200, {"id": str(m.id), "subject": body.subject, "roles": m.roles}
    return mutate(request, session, principal, body.model_dump(mode="json"), run)


@router.post("/projects/{project_id}/repository")
def link_repository(project_id: uuid.UUID, body: schemas.RepositoryLink, request: Request,
                    session: Session = Depends(get_session),
                    principal: Principal = Depends(current_principal)):
    ctx = make_ctx(request, session, principal)
    inspector = request.app.state.repository_inspector

    def run():
        repo = project_svc.configure_repository(
            ctx, project_id, installation_id=body.installation_id,
            repository_id=body.repository_id, base_branch=body.base_branch, inspector=inspector)
        project = session.get(Project, project_id)
        readiness = project_svc.activation_readiness(ctx, project)
        return 200, {"repository_id": str(repo.id), "verification": repo.verification,
                     "readiness": [i.__dict__ for i in readiness]}
    return mutate(request, session, principal, body.model_dump(mode="json"), run)


@router.post("/projects/{project_id}/agents", status_code=201)
def create_agent_config(project_id: uuid.UUID, body: schemas.AgentConfigCreate, request: Request,
                        session: Session = Depends(get_session),
                        principal: Principal = Depends(current_principal)):
    ctx = make_ctx(request, session, principal)

    def run():
        c = project_svc.create_agent_config(ctx, project_id, body.model_dump(mode="json"))
        return 201, {"id": str(c.id), "role": c.role, "version": c.version,
                     "provider": c.provider, "model": c.model,
                     "connection_test": c.connection_test}
    return mutate(request, session, principal, body.model_dump(mode="json"), run)


@router.put("/projects/{project_id}/budgets")
def set_budget(project_id: uuid.UUID, body: schemas.BudgetSet, request: Request,
               session: Session = Depends(get_session),
               principal: Principal = Depends(current_principal)):
    ctx = make_ctx(request, session, principal)

    def run():
        b = project_svc.set_budget(ctx, project_id, scope=body.scope, period=body.period,
                                   cap=body.cap)
        return 200, {"id": str(b.id), "scope": b.scope, "period": b.period, "cap": str(b.cap),
                     "currency": b.currency}
    return mutate(request, session, principal, body.model_dump(mode="json"), run)


@router.get("/projects/{project_id}/readiness")
def readiness(project_id: uuid.UUID, request: Request, session: Session = Depends(get_session),
              principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    ctx = make_ctx(request, session, principal)
    project = project_svc.get_project(ctx, project_id)
    items = project_svc.activation_readiness(ctx, project)
    return {"ready": all(i.ok for i in items), "items": [i.__dict__ for i in items]}


@router.post("/projects/{project_id}/activate")
def activate(project_id: uuid.UUID, body: schemas.ActivateBody, request: Request,
             session: Session = Depends(get_session),
             principal: Principal = Depends(current_principal)):
    ctx = make_ctx(request, session, principal)

    def run():
        p, _ = project_svc.activate(ctx, project_id, expected_version=body.expected_version)
        return 200, _project_json(p)
    return mutate(request, session, principal, body.model_dump(mode="json"), run)


# ---------------------------------------------------------------- tasks


@router.post("/projects/{project_id}/tasks", status_code=201)
def create_task(project_id: uuid.UUID, body: schemas.TaskCreate, request: Request,
                session: Session = Depends(get_session),
                principal: Principal = Depends(current_principal)):
    ctx = make_ctx(request, session, principal)

    def run():
        t = task_svc.create_task(ctx, project_id, title=body.title, description=body.description,
                                 priority=body.priority, dependencies=body.dependencies)
        return 201, _task_card(t)
    return mutate(request, session, principal, body.model_dump(mode="json"), run)


@router.get("/projects/{project_id}/tasks")
def list_tasks(project_id: uuid.UUID, request: Request, cursor: str | None = None,
               limit: int | None = Query(default=None, ge=1, le=100), stage: str | None = None,
               session: Session = Depends(get_session),
               principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    ctx = make_ctx(request, session, principal)
    ctx.require(Action.TASK_READ, project_id, object_type="project", object_id=project_id)
    n = _page_limit(limit)
    stmt = select(Task).where(Task.project_id == project_id, Task.deleted_at.is_(None))
    if stage:
        stmt = stmt.where(Task.stage == stage)
    if c := decode_cursor(cursor):
        ts, tid = datetime.fromisoformat(c["t"]), uuid.UUID(c["id"])
        stmt = stmt.where(or_(Task.created_at < ts, and_(Task.created_at == ts, Task.id < tid)))
    rows = session.scalars(stmt.order_by(Task.created_at.desc(), Task.id.desc()).limit(n + 1)).all()
    items = rows[:n]
    next_cursor = (encode_cursor({"t": items[-1].created_at.isoformat(), "id": str(items[-1].id)})
                   if len(rows) > n else None)
    return {"items": [_task_card(t) for t in items], "next_cursor": next_cursor}


@router.get("/projects/{project_id}/board")
def board(project_id: uuid.UUID, request: Request, session: Session = Depends(get_session),
          principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    ctx = make_ctx(request, session, principal)
    ctx.require(Action.TASK_READ, project_id, object_type="project", object_id=project_id)
    rows = session.scalars(select(Task).where(Task.project_id == project_id, Task.deleted_at.is_(None))
                           .order_by(Task.priority, Task.created_at).limit(500)).all()
    columns: dict[str, list[dict[str, Any]]] = {c: [] for c in KANBAN_COLUMNS}
    for t in rows:
        columns[KANBAN_COLUMN_FOR_STAGE[t.stage]].append(_task_card(t))
    return {"columns": [{"name": c, "items": columns[c]} for c in KANBAN_COLUMNS]}


@router.get("/tasks/{task_id}")
def get_task(task_id: uuid.UUID, request: Request, session: Session = Depends(get_session),
             principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    return task_svc.task_view(make_ctx(request, session, principal), task_id)


@router.post("/tasks/{task_id}/commands", status_code=202)
def task_command(task_id: uuid.UUID, body: schemas.CommandBody, request: Request,
                 session: Session = Depends(get_session),
                 principal: Principal = Depends(current_principal)):
    ctx = make_ctx(request, session, principal)

    def run():
        t = task_svc.run_command(ctx, task_id, command=body.command,
                                 expected_version=body.expected_version, reason=body.reason,
                                 additional_repairs=body.additional_repairs)
        return 202, {"operation": body.command, "correlation_id": ctx.correlation_id,
                     "task": _task_card(t)}
    return mutate(request, session, principal, body.model_dump(mode="json"), run)


@router.post("/tasks/{task_id}/requirements", status_code=201)
def submit_requirements(task_id: uuid.UUID, body: schemas.RequirementsBody, request: Request,
                        session: Session = Depends(get_session),
                        principal: Principal = Depends(current_principal)):
    ctx = make_ctx(request, session, principal)

    def run():
        try:
            spec = BASpecification.model_validate(body.payload)
        except ValidationError as exc:
            raise Unprocessable(
                "requirements payload fails the BA schema", code="schema_invalid",
                field_errors={".".join(str(p) for p in e["loc"]) or "payload": e["msg"]
                              for e in exc.errors()}) from exc
        t, v, created = task_svc.submit_requirements(
            ctx, task_id, spec=spec, expected_version=body.expected_version,
            source=body.source, provenance=body.provenance)
        return (201 if created else 200), {
            "created": created, "spec_id": str(v.id), "version": v.version,
            "content_hash": v.content_hash, "task": _task_card(t),
            "open_blocking_questions": len(spec.open_blocking_questions)}
    return mutate(request, session, principal, body.model_dump(mode="json"), run)


@router.post("/tasks/{task_id}/approvals")
def decide(task_id: uuid.UUID, body: schemas.ApprovalBody, request: Request,
           session: Session = Depends(get_session),
           principal: Principal = Depends(current_principal)):
    ctx = make_ctx(request, session, principal)

    def run():
        t = task_svc.decide_gate(ctx, task_id, gate=body.gate, decision=body.decision,
                                 scope_hash=body.scope_hash,
                                 expected_version=body.expected_version, reason=body.reason)
        return 200, {"task": _task_card(t)}
    return mutate(request, session, principal, body.model_dump(mode="json"), run)


# ---------------------------------------------------------------- events and audit


@router.get("/projects/{project_id}/events")
def events(project_id: uuid.UUID, request: Request, after: int = Query(default=0, ge=0),
           limit: int | None = Query(default=None, ge=1, le=100),
           session: Session = Depends(get_session),
           principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    ctx = make_ctx(request, session, principal)
    ctx.require(Action.PROJECT_READ, project_id, object_type="project", object_id=project_id)
    n = _page_limit(limit)
    rows = session.scalars(select(OutboxEvent).where(
        OutboxEvent.project_id == project_id, OutboxEvent.seq > after
    ).order_by(OutboxEvent.seq).limit(n)).all()
    return {"items": [envelope(e) for e in rows],
            "next_cursor": rows[-1].seq if rows else after}


@router.get("/projects/{project_id}/audit")
def audit_log(project_id: uuid.UUID, request: Request, before: int | None = None,
              limit: int | None = Query(default=None, ge=1, le=100),
              action: str | None = None, actor: str | None = None, object_id: str | None = None,
              outcome: str | None = None, session: Session = Depends(get_session),
              principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    ctx = make_ctx(request, session, principal)
    ctx.require(Action.AUDIT_READ, project_id, object_type="audit", object_id=project_id)
    n = _page_limit(limit)
    stmt = select(AuditEvent).where(AuditEvent.project_id == project_id)
    if before:
        stmt = stmt.where(AuditEvent.seq < before)
    if action:
        stmt = stmt.where(AuditEvent.action.startswith(action))
    if actor:
        stmt = stmt.where(AuditEvent.actor_id == actor)
    if object_id:
        stmt = stmt.where(AuditEvent.object_id == object_id)
    if outcome:
        stmt = stmt.where(AuditEvent.outcome == outcome)
    rows = session.scalars(stmt.order_by(AuditEvent.seq.desc()).limit(n + 1)).all()
    items = rows[:n]
    return {
        "items": [{"seq": e.seq, "event_id": str(e.event_id), "actor_kind": e.actor_kind,
                   "actor_id": e.actor_id, "action": e.action, "object_type": e.object_type,
                   "object_id": e.object_id, "outcome": e.outcome, "reason": e.reason,
                   "correlation_id": e.correlation_id, "policy_version": e.policy_version,
                   "created_at": e.created_at.isoformat()} for e in items],
        "next_cursor": items[-1].seq if len(rows) > n else None,
    }


# ---------------------------------------------------------------- execution policy


@router.put("/projects/{project_id}/execution-policy")
def set_execution_policy(project_id: uuid.UUID, body: schemas.ExecutionPolicy, request: Request,
                         session: Session = Depends(get_session),
                         principal: Principal = Depends(current_principal)):
    ctx = make_ctx(request, session, principal)

    def run():
        p = project_svc.set_execution_policy(
            ctx, project_id, commands=[c.model_dump() for c in body.commands],
            protected_paths=body.protected_paths, expected_version=body.expected_version)
        return 200, {**_project_json(p), "policy": p.policy}
    return mutate(request, session, principal, body.model_dump(mode="json"), run)


# ---------------------------------------------------------------- team and runs (FR-12)


@router.get("/projects/{project_id}/team")
def team(project_id: uuid.UUID, request: Request, session: Session = Depends(get_session),
         principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    ctx = make_ctx(request, session, principal)
    project = project_svc.get_project(ctx, project_id)
    return {"members": orchestrator.team_view(ctx, project), "policy": project.policy}


@router.get("/tasks/{task_id}/runs")
def task_runs(task_id: uuid.UUID, request: Request, session: Session = Depends(get_session),
              principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    ctx = make_ctx(request, session, principal)
    task, _ = task_svc.load_task(ctx, task_id)
    runs = session.scalars(select(Run).where(Run.task_id == task.id).order_by(Run.created_at.desc())
                           .limit(50)).all()
    return {"items": [orchestrator.run_view(ctx, r, with_logs=True) for r in runs]}


@router.get("/tasks/{task_id}/qa")
def task_qa(task_id: uuid.UUID, request: Request, session: Session = Depends(get_session),
            principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    ctx = make_ctx(request, session, principal)
    task, _ = task_svc.load_task(ctx, task_id)
    reports = session.scalars(select(QAReportRecord).where(QAReportRecord.task_id == task.id)
                              .order_by(QAReportRecord.created_at.desc()).limit(20)).all()
    defects = session.scalars(select(Defect).where(Defect.task_id == task.id)
                              .order_by(Defect.status, Defect.created_at)).all()
    return {
        "current_report_id": str(task.current_qa_report_id) if task.current_qa_report_id else None,
        "reports": [{"id": str(r.id), "verdict": r.verdict, "head_sha": r.head_sha, "base_sha": r.base_sha,
                     "current": r.head_sha == task.head_sha and r.base_sha == task.base_sha,
                     "payload": r.payload, "created_at": r.created_at.isoformat()} for r in reports],
        "defects": [{"id": str(d.id), "ac_id": d.ac_id, "severity": d.severity, "status": d.status,
                     "title": d.title, "evidence": d.evidence, "updated_at": d.updated_at.isoformat()}
                    for d in defects],
    }


@router.get("/tasks/{task_id}/ba-brief")
def ba_brief(task_id: uuid.UUID, request: Request, session: Session = Depends(get_session),
             principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    ctx = make_ctx(request, session, principal)
    task, project = task_svc.load_task(ctx, task_id)
    return {"markdown": orchestrator.ba_brief(ctx, task, project)}


# ---------------------------------------------------------------- worker protocol


@router.post("/workers/hello")
def worker_hello(body: schemas.WorkerHello, request: Request, session: Session = Depends(get_session),
                 principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    ctx = make_ctx(request, session, principal)
    orchestrator.record_worker(ctx, worker_id=body.worker_id, capabilities=body.capabilities,
                               version=body.version)
    session.commit()
    return {"ok": True}


@router.post("/workers/claim")
def worker_claim(body: schemas.WorkerClaim, request: Request, session: Session = Depends(get_session),
                 principal: Principal = Depends(current_principal)):
    ctx = make_ctx(request, session, principal)

    def run():
        claimed = orchestrator.claim(ctx, worker_id=body.worker_id, roles=list(body.roles))
        return 200, {"run": claimed}
    return mutate(request, session, principal, body.model_dump(mode="json"), run)


@router.post("/runs/{run_id}/heartbeat")
def run_heartbeat(run_id: uuid.UUID, body: schemas.RunHeartbeat, request: Request,
                  session: Session = Depends(get_session),
                  principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    ctx = make_ctx(request, session, principal)
    try:
        out = orchestrator.heartbeat(ctx, run_id, claim_token=body.claim_token,
                                     milestone=body.milestone, logs=body.logs)
        session.commit()
    except Exception:
        session.rollback()
        raise
    return out


@router.post("/runs/{run_id}/junior")
def run_junior(run_id: uuid.UUID, body: schemas.JuniorRequest, request: Request,
               session: Session = Depends(get_session), principal: Principal = Depends(current_principal)):
    ctx = make_ctx(request, session, principal)

    def run():
        return 200, {"items": orchestrator.request_junior(ctx, run_id, claim_token=body.claim_token,
                                                         assignments=body.assignments)}
    return mutate(request, session, principal, body.model_dump(mode="json"), run)


@router.post("/runs/{run_id}/results")
def run_result(run_id: uuid.UUID, body: schemas.RunResultBody, request: Request,
               session: Session = Depends(get_session), principal: Principal = Depends(current_principal)):
    ctx = make_ctx(request, session, principal)

    def run():
        out = orchestrator.submit_result(ctx, run_id, claim_token=body.claim_token, outcome=body.outcome,
                                         payload=body.payload, usage=body.usage, errors=body.errors,
                                         provider=body.provider, model=body.model)
        return 200, out
    return mutate(request, session, principal, body.model_dump(mode="json"), run)

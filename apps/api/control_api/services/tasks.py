"""Ticket commands (FR-02, FR-09/10, FR-14, FR-15).

Every mutation follows the same transaction: lock the task row, verify the principal
and the expected version, evaluate guards against persisted facts, mutate, append an
audit event, write an outbox event. The caller commits once.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from typing import Any

from sqlalchemy import and_, exists, func, select
from sqlalchemy.orm import aliased

from control_api.contracts import BASpecification
from control_api.db.models import (
    AcceptanceCriterion,
    Approval,
    Project,
    RequirementVersion,
    Task,
    TaskDependency,
    utcnow,
)
from control_api.domain.approvals import (
    ApprovalDecision,
    Gate,
    canonical_hash,
    merge_scope,
    requirements_scope,
)
from control_api.domain.lifecycle import (
    PRE_MERGE_STAGES,
    TERMINAL_STAGES,
    Decision,
    ExecutionStatus,
    GuardFacts,
    Stage,
    Trigger,
    evaluate,
)
from control_api.domain.permissions import Action, authorize
from control_api.errors import Conflict, NotFound, Unprocessable
from control_api.services import leases
from control_api.services.audit import audit
from control_api.services.context import Ctx
from control_api.services.outbox import emit

MAX_ADDITIONAL_REPAIRS = 3


def snapshot(t: Task) -> dict[str, Any]:
    return {
        "id": str(t.id), "key": t.key, "stage": t.stage, "execution_status": t.execution_status,
        "version": t.version, "current_spec_id": str(t.current_spec_id or ""),
        "approved_spec_id": str(t.approved_spec_id or ""), "head_sha": t.head_sha,
        "base_sha": t.base_sha, "repair_count": t.repair_count, "repair_limit": t.repair_limit,
        "priority": t.priority, "title": t.title,
    }


# ---------------------------------------------------------------- loading


def load_task(
    ctx: Ctx, task_id: uuid.UUID, action: Action = Action.TASK_READ, *, for_update: bool = False
) -> tuple[Task, Project]:
    stmt = select(Task).where(Task.id == task_id)
    if for_update:
        stmt = stmt.with_for_update()
    task = ctx.session.scalar(stmt)
    if task is None:
        raise NotFound("task not found")
    # Membership is checked before anything about the task is revealed (AT-15).
    ctx.require(action, task.project_id, object_type="task", object_id=task.id)
    project = ctx.session.get(Project, task.project_id)
    assert project is not None
    return task, project


def _check_version(task: Task, expected_version: int | None) -> None:
    if expected_version is None:
        raise Unprocessable("expected_version is required",
                            field_errors={"expected_version": "required"})
    if task.version != expected_version:
        raise Conflict(f"task is at version {task.version}, not {expected_version}; refresh",
                       code="stale_version", details={"current_version": task.version})


# ---------------------------------------------------------------- creation


def create_task(
    ctx: Ctx, project_id: uuid.UUID, *, title: str, description: str, priority: str,
    dependencies: list[uuid.UUID],
) -> Task:
    ctx.require(Action.TASK_CREATE, project_id, object_type="task")
    project = ctx.session.scalar(select(Project).where(Project.id == project_id).with_for_update())
    if project is None:
        raise NotFound("project not found")
    if project.status == "ARCHIVED":
        raise Conflict("project is archived")
    deps = sorted(set(dependencies))
    if deps:
        found = set(ctx.session.scalars(select(Task.id).where(
            Task.id.in_(deps), Task.project_id == project.id)).all())
        if missing := [str(d) for d in deps if d not in found]:
            # Concealed: a foreign project's ticket is indistinguishable from a missing one.
            raise Unprocessable("dependencies must be existing tickets in the same project",
                                field_errors={"dependencies": ", ".join(missing)})
    project.task_seq += 1
    task = Task(project_id=project.id, key=f"{project.key}-{project.task_seq}", title=title,
                description=description, priority=priority,
                owner_id=uuid.UUID(ctx.principal.id))
    ctx.session.add(task)
    ctx.session.flush()
    for dep in deps:
        ctx.session.add(TaskDependency(task_id=task.id, depends_on_id=dep))
    audit(ctx.session, ctx.principal, action="task.create", object_type="task", object_id=task.id,
          project_id=project.id, correlation_id=ctx.correlation_id, after=snapshot(task),
          policy_version=project.policy_version)
    emit(ctx.session, project_id=project.id, aggregate_type="task", aggregate_id=task.id,
         aggregate_version=task.version, type="task.created", payload=snapshot(task))
    return task


# ---------------------------------------------------------------- facts and transitions


def _spec(ctx: Ctx, spec_id: uuid.UUID | None) -> RequirementVersion | None:
    return ctx.session.get(RequirementVersion, spec_id) if spec_id else None


def open_blocking_questions(spec: RequirementVersion | None) -> list[dict[str, Any]]:
    if spec is None:
        return []
    return [q for q in spec.payload.get("questions", [])
            if q.get("blocking") and not (q.get("resolution") or "").strip()]


def gather_facts(ctx: Ctx, task: Task, project: Project, reason: str | None = None) -> GuardFacts:
    spec = _spec(ctx, task.current_spec_id)
    deps_open = ctx.session.scalar(
        select(func.count()).select_from(TaskDependency).join(
            Task, Task.id == TaskDependency.depends_on_id
        ).where(TaskDependency.task_id == task.id, Task.stage != Stage.DONE.value)
    )
    return GuardFacts(
        project_active=project.status == "ACTIVE",
        inputs_valid=bool(task.title.strip()),
        reason=reason,
        spec_present=spec is not None,
        spec_immutable=spec is not None and task.approved_spec_id == spec.id,
        blocking_questions_open=len(open_blocking_questions(spec)),
        dependencies_done=deps_open == 0,
        repair_count=task.repair_count,
        repair_limit=task.repair_limit,
        # Populated by the dispatcher, Git broker and QA pipeline (vertical-slice stage).
        budget_reserved=False,
        lease_acquired=False,
        qa_mandatory_all_pass=False,
        qa_evidence_current=False,
        ci_required_pass=False,
        merge_confirmed_by_github=False,
        pending_external_effects=False,
    )


def _refuse(decision: Decision) -> Conflict:
    return Conflict(
        f"{decision.trigger} refused: {decision.explanation}",
        code="illegal_transition",
        details={"stage": str(decision.from_stage), "unmet": list(decision.unmet)},
    )


def apply_transition(
    ctx: Ctx, task: Task, project: Project, trigger: Trigger, facts: GuardFacts,
    *, reason: str | None = None, details: dict[str, Any] | None = None,
) -> Decision:
    decision = evaluate(Stage(task.stage), trigger, facts)
    if not decision.allowed:
        raise _refuse(decision)
    assert decision.to_stage is not None and decision.status is not None
    before = snapshot(task)
    task.stage = decision.to_stage.value
    task.execution_status = decision.status.value
    task.status_reason = reason
    task.version += 1
    task.updated_at = utcnow()
    payload = {"from": before["stage"], "to": task.stage, "trigger": str(trigger),
               "execution_status": task.execution_status, "version": task.version,
               **(details or {})}
    audit(ctx.session, ctx.principal, action=f"task.transition.{trigger.value.lower()}",
          object_type="task", object_id=task.id, project_id=project.id,
          correlation_id=ctx.correlation_id, reason=reason, before=before, after=snapshot(task),
          policy_version=project.policy_version, details=payload)
    emit(ctx.session, project_id=project.id, aggregate_type="task", aggregate_id=task.id,
         aggregate_version=task.version, type="task.transitioned", payload=payload)
    return decision


def _active_approvals(ctx: Ctx, task: Task, gates: set[Gate]) -> list[Approval]:
    newer = aliased(Approval)
    return list(ctx.session.scalars(
        select(Approval).where(
            Approval.task_id == task.id,
            Approval.gate.in_([g.value for g in gates]),
            Approval.decision == ApprovalDecision.APPROVED.value,
            ~exists().where(newer.supersedes_id == Approval.id),
        )
    ))


def _revoke(ctx: Ctx, task: Task, project: Project, gates: set[Gate], why: str) -> int:
    revoked = _active_approvals(ctx, task, gates)
    for approval in revoked:
        ctx.session.add(Approval(
            project_id=project.id, task_id=task.id, gate=approval.gate,
            decision=ApprovalDecision.REVOKED.value, scope_hash=approval.scope_hash,
            scope=approval.scope, human_id=None, reason=why, supersedes_id=approval.id,
        ))
        audit(ctx.session, ctx.principal, action="approval.revoke", object_type="approval",
              object_id=approval.id, project_id=project.id, correlation_id=ctx.correlation_id,
              reason=why, policy_version=project.policy_version, details={"gate": approval.gate})
    return len(revoked)


# ---------------------------------------------------------------- commands


COMMANDS = ("analyze", "request_changes", "cancel", "resume", "retry")


def run_command(
    ctx: Ctx, task_id: uuid.UUID, *, command: str, expected_version: int | None,
    reason: str | None = None, additional_repairs: int | None = None,
) -> Task:
    if command not in COMMANDS:
        raise Unprocessable(f"unknown command {command!r}", field_errors={"command": command})
    task, project = load_task(ctx, task_id, for_update=True)
    stage = Stage(task.stage)

    if command == "analyze":
        ctx.require(Action.TASK_ANALYZE, project.id, object_type="task", object_id=task.id)
        _check_version(task, expected_version)
        apply_transition(ctx, task, project, Trigger.START_ANALYSIS,
                         gather_facts(ctx, task, project, reason), reason=reason)
        return task

    if command == "request_changes":
        gate = Gate.MERGE if stage is Stage.MERGE_APPROVAL else Gate.REQUIREMENTS
        return decide_gate(ctx, task_id, gate=gate, decision=ApprovalDecision.CHANGES_REQUESTED,
                           scope_hash=None, expected_version=expected_version, reason=reason,
                           _loaded=(task, project))

    if command == "cancel":
        ctx.require(Action.TASK_CANCEL, project.id, object_type="task", object_id=task.id)
        _check_version(task, expected_version)
        apply_transition(ctx, task, project, Trigger.CANCEL,
                         gather_facts(ctx, task, project, reason), reason=reason)
        fenced = leases.release(ctx.session, task_id=task.id, reason="CANCELLED")
        _revoke(ctx, task, project, {Gate.REQUIREMENTS, Gate.MERGE}, "ticket cancelled")
        audit(ctx.session, ctx.principal, action="task.cancel.cleanup", object_type="task",
              object_id=task.id, project_id=project.id, correlation_id=ctx.correlation_id,
              details={"leases_fenced": fenced, "source_branches_deleted": False})
        return task

    # resume / retry keep the stage and change only execution status.
    action = Action.TASK_RESUME if command == "resume" else Action.TASK_RETRY
    ctx.require(action, project.id, object_type="task", object_id=task.id)
    _check_version(task, expected_version)
    if stage in TERMINAL_STAGES:
        raise Conflict(f"{stage} is terminal; open a linked ticket", code="illegal_transition")
    if not (reason and reason.strip()):
        raise Unprocessable("a reason is required", field_errors={"reason": "required"})
    status = ExecutionStatus(task.execution_status)
    before = snapshot(task)
    if command == "resume":
        if status not in (ExecutionStatus.PAUSED, ExecutionStatus.BLOCKED):
            raise Conflict(f"resume requires PAUSED or BLOCKED, task is {status}",
                           code="illegal_transition")
        if stage is Stage.FIX_REQUIRED and status is ExecutionStatus.PAUSED:
            # A human grants a new bounded allowance; it never turns a failure into a pass.
            if not additional_repairs or not 1 <= additional_repairs <= MAX_ADDITIONAL_REPAIRS:
                raise Unprocessable(
                    f"additional_repairs must be 1-{MAX_ADDITIONAL_REPAIRS} to resume repairs",
                    field_errors={"additional_repairs": "required"})
            task.repair_limit = task.repair_count + additional_repairs
        task.execution_status = ExecutionStatus.IDLE.value
    else:
        if status not in (ExecutionStatus.FAILED, ExecutionStatus.BLOCKED):
            raise Conflict(f"retry requires FAILED or BLOCKED, task is {status}",
                           code="illegal_transition")
        task.execution_status = ExecutionStatus.QUEUED.value
    task.status_reason = reason
    task.version += 1
    task.updated_at = utcnow()
    audit(ctx.session, ctx.principal, action=f"task.{command}", object_type="task",
          object_id=task.id, project_id=project.id, correlation_id=ctx.correlation_id,
          reason=reason, before=before, after=snapshot(task),
          policy_version=project.policy_version)
    emit(ctx.session, project_id=project.id, aggregate_type="task", aggregate_id=task.id,
         aggregate_version=task.version,
         type={"resume": "task.resumed", "retry": "task.retried"}[command],
         payload=snapshot(task))
    return task


# ---------------------------------------------------------------- requirements


def submit_requirements(
    ctx: Ctx, task_id: uuid.UUID, *, spec: BASpecification, expected_version: int | None,
    source: str, provenance: dict[str, Any] | None = None,
) -> tuple[Task, RequirementVersion, bool]:
    """Create a new immutable spec version. Returns (task, version, created)."""
    task, project = load_task(ctx, task_id, Action.REQUIREMENTS_DRAFT_WRITE, for_update=True)
    _check_version(task, expected_version)
    stage = Stage(task.stage)
    if not ctx.principal.is_human and stage is not Stage.BA_ANALYSIS:
        raise Conflict("agents may only submit requirements during BA_ANALYSIS",
                       code="illegal_transition")
    if stage not in PRE_MERGE_STAGES and stage is not Stage.BA_ANALYSIS:
        raise Conflict(f"requirements cannot change in {stage}", code="illegal_transition")

    payload = spec.model_dump(mode="json")
    content_hash = canonical_hash(payload)
    current = _spec(ctx, task.current_spec_id)
    if current is not None and current.content_hash == content_hash:
        return task, current, False

    next_version = (ctx.session.scalar(select(func.max(RequirementVersion.version)).where(
        RequirementVersion.task_id == task.id)) or 0) + 1
    version = RequirementVersion(
        project_id=project.id, task_id=task.id, version=next_version, content_hash=content_hash,
        payload=payload, source=source, provenance=provenance or {},
        created_by_kind=str(ctx.principal.kind), created_by=ctx.principal.id,
    )
    ctx.session.add(version)
    ctx.session.flush()
    for ac in spec.acceptance_criteria:
        ctx.session.add(AcceptanceCriterion(spec_id=version.id, stable_key=ac.id,
                                            statement=ac.statement,
                                            verification=ac.verification,
                                            mandatory=ac.mandatory))
    audit(ctx.session, ctx.principal, action="requirements.version.create",
          object_type="requirements", object_id=version.id, project_id=project.id,
          correlation_id=ctx.correlation_id, after={"version": next_version,
                                                     "content_hash": content_hash},
          policy_version=project.policy_version, details={"source": source,
                                                          "task_id": str(task.id)})
    if stage in PRE_MERGE_STAGES and stage is not Stage.REQUIREMENTS_APPROVAL:
        # An approved baseline changed: revoke, fence obsolete work, re-enter analysis (AT-03).
        _revoke(ctx, task, project, {Gate.REQUIREMENTS, Gate.MERGE},
                f"requirements changed to v{next_version}")
        leases.release(ctx.session, task_id=task.id, reason="REQUIREMENTS_CHANGED")
        task.approved_spec_id = None
        apply_transition(ctx, task, project, Trigger.REQUIREMENTS_CHANGED,
                         gather_facts(ctx, task, project),
                         reason=f"requirements changed to v{next_version}")
        stage = Stage(task.stage)

    task.current_spec_id = version.id
    if stage is Stage.BA_ANALYSIS:
        apply_transition(ctx, task, project, Trigger.BA_RESULT_VALID,
                         gather_facts(ctx, task, project),
                         details={"spec_version": next_version})
    else:  # REQUIREMENTS_APPROVAL: a newer draft replaces the one awaiting approval
        task.version += 1
        task.updated_at = utcnow()
    emit(ctx.session, project_id=project.id, aggregate_type="task", aggregate_id=task.id,
         aggregate_version=task.version, type="requirements.version_created",
         payload={"spec_id": str(version.id), "version": next_version, "source": source})
    return task, version, True


# ---------------------------------------------------------------- gates


def current_scope(ctx: Ctx, task: Task, project: Project, gate: Gate) -> tuple[str, dict] | None:
    if gate is Gate.REQUIREMENTS:
        spec = _spec(ctx, task.current_spec_id)
        if spec is None:
            return None
        scope = {"spec_id": str(spec.id), "spec_version": spec.version,
                 "content_hash": spec.content_hash}
        return requirements_scope(spec.version, spec.content_hash), scope
    if gate is Gate.MERGE:
        spec = _spec(ctx, task.approved_spec_id)
        if not (spec and task.head_sha and task.base_sha and task.current_qa_report_id):
            return None
        scope = {"spec_hash": spec.content_hash, "head_sha": task.head_sha,
                 "base_sha": task.base_sha, "qa_report_id": str(task.current_qa_report_id),
                 "policy_version": project.policy_version}
        return merge_scope(**scope), scope
    return None


_GATE_ACTION = {Gate.REQUIREMENTS: Action.APPROVE_REQUIREMENTS, Gate.MERGE: Action.APPROVE_MERGE}
_GATE_STAGE = {Gate.REQUIREMENTS: Stage.REQUIREMENTS_APPROVAL, Gate.MERGE: Stage.MERGE_APPROVAL}
_CHANGES_ACTION = {Gate.REQUIREMENTS: Action.REQUIREMENTS_REQUEST_CHANGES,
                   Gate.MERGE: Action.CODE_REQUEST_CHANGES}


def decide_gate(
    ctx: Ctx, task_id: uuid.UUID, *, gate: Gate, decision: ApprovalDecision,
    scope_hash: str | None, expected_version: int | None, reason: str | None,
    _loaded: tuple[Task, Project] | None = None,
) -> Task:
    if gate is Gate.RELEASE:
        raise Unprocessable("release decisions use /v1/releases/{id}/approvals")
    if decision not in (ApprovalDecision.APPROVED, ApprovalDecision.CHANGES_REQUESTED):
        raise Unprocessable("decision must be APPROVED or CHANGES_REQUESTED")
    approving = decision is ApprovalDecision.APPROVED
    action = _GATE_ACTION[gate] if approving else _CHANGES_ACTION[gate]
    # Agents and services are refused here, before any state is examined.
    if _loaded:
        task, project = _loaded
        ctx.require(action, project.id, object_type="approval", object_id=task.id)
    else:
        task, project = load_task(ctx, task_id, action, for_update=True)
    _check_version(task, expected_version)
    if Stage(task.stage) is not _GATE_STAGE[gate]:
        raise Conflict(f"{gate} decision requires stage {_GATE_STAGE[gate]}, task is {task.stage}",
                       code="illegal_transition")

    facts = gather_facts(ctx, task, project, reason)
    if approving:
        trigger = Trigger.APPROVE_REQUIREMENTS if gate is Gate.REQUIREMENTS else Trigger.APPROVE_MERGE
        bound = current_scope(ctx, task, project, gate)
        if bound is None:
            probe = evaluate(Stage(task.stage), trigger, facts)
            raise _refuse(probe) if not probe.allowed else Conflict("nothing to approve")
        expected_hash, scope = bound
        if scope_hash != expected_hash:
            raise Conflict("approval scope is stale; review the current version",
                           code="stale_scope", details={"current_scope": scope,
                                                        "current_scope_hash": expected_hash})
    else:
        bound = current_scope(ctx, task, project, gate)
        expected_hash, scope = bound if bound else (canonical_hash({"task": str(task.id)}), {})
        trigger = (Trigger.REQUEST_REQUIREMENT_CHANGES if gate is Gate.REQUIREMENTS
                   else Trigger.REQUEST_CODE_CHANGES)

    # Evaluate before recording so a refused approval leaves no approval row.
    probe = evaluate(Stage(task.stage), trigger, facts)
    if not probe.allowed:
        raise _refuse(probe)
    approval = Approval(project_id=project.id, task_id=task.id, gate=gate.value,
                        decision=decision.value, scope_hash=expected_hash, scope=scope,
                        human_id=uuid.UUID(ctx.principal.id), reason=reason)
    ctx.session.add(approval)
    ctx.session.flush()
    if approving and gate is Gate.REQUIREMENTS:
        task.approved_spec_id = task.current_spec_id
    audit(ctx.session, ctx.principal, action=f"approval.{gate.value.lower()}.{decision.value.lower()}",
          object_type="approval", object_id=approval.id, project_id=project.id,
          correlation_id=ctx.correlation_id, reason=reason,
          policy_version=project.policy_version,
          details={"scope_hash": expected_hash, "scope": scope, "task_id": str(task.id)})
    apply_transition(ctx, task, project, trigger, facts, reason=reason,
                     details={"approval_id": str(approval.id), "gate": gate.value})
    return task


# ---------------------------------------------------------------- read models


def permitted_actions(ctx: Ctx, task: Task, project: Project) -> list[dict[str, Any]]:
    """What this principal may do next, and exactly why not otherwise (PRD §4)."""
    stage = Stage(task.stage)
    status = ExecutionStatus(task.execution_status)
    facts = replace(gather_facts(ctx, task, project), reason="(provided at submission)")
    out: list[dict[str, Any]] = []

    def add(command: str, action: Action, trigger: Trigger | None, extra: list[str]) -> None:
        reasons = list(extra)
        authz = authorize(ctx.principal, action, str(project.id))
        if not authz.allowed:
            reasons.append(authz.reason)
        if trigger is not None:
            d = evaluate(stage, trigger, facts)
            reasons.extend(d.unmet)
        out.append({"command": command, "allowed": not reasons, "reasons": reasons})

    add("analyze", Action.TASK_ANALYZE, Trigger.START_ANALYSIS, [])
    add("approve_requirements", Action.APPROVE_REQUIREMENTS, Trigger.APPROVE_REQUIREMENTS, [])
    if stage is Stage.MERGE_APPROVAL:
        add("request_changes", Action.CODE_REQUEST_CHANGES, Trigger.REQUEST_CODE_CHANGES, [])
    else:
        add("request_changes", Action.REQUIREMENTS_REQUEST_CHANGES,
            Trigger.REQUEST_REQUIREMENT_CHANGES, [])
    add("approve_merge", Action.APPROVE_MERGE, Trigger.APPROVE_MERGE, [])
    add("cancel", Action.TASK_CANCEL, Trigger.CANCEL, [])
    add("resume", Action.TASK_RESUME, None,
        [] if status in (ExecutionStatus.PAUSED, ExecutionStatus.BLOCKED) and
        stage not in TERMINAL_STAGES else [f"execution status is {status}, not PAUSED/BLOCKED"])
    add("retry", Action.TASK_RETRY, None,
        [] if status in (ExecutionStatus.FAILED, ExecutionStatus.BLOCKED) and
        stage not in TERMINAL_STAGES else [f"execution status is {status}, not FAILED/BLOCKED"])
    return out


def task_view(ctx: Ctx, task_id: uuid.UUID) -> dict[str, Any]:
    task, project = load_task(ctx, task_id)
    s = ctx.session
    specs = s.scalars(select(RequirementVersion).where(RequirementVersion.task_id == task.id)
                      .order_by(RequirementVersion.version)).all()
    current = next((v for v in specs if v.id == task.current_spec_id), None)
    approvals = s.scalars(select(Approval).where(Approval.task_id == task.id)
                          .order_by(Approval.created_at)).all()
    deps = s.execute(select(Task.id, Task.key, Task.stage).join(
        TaskDependency, and_(TaskDependency.depends_on_id == Task.id,
                             TaskDependency.task_id == task.id))).all()
    req_scope = current_scope(ctx, task, project, Gate.REQUIREMENTS)
    return {
        "task": {**snapshot(task), "project_id": str(project.id), "project_key": project.key,
                 "description": task.description, "status_reason": task.status_reason,
                 "owner_id": str(task.owner_id), "created_at": task.created_at.isoformat(),
                 "updated_at": task.updated_at.isoformat()},
        "dependencies": [{"id": str(d.id), "key": d.key, "stage": d.stage} for d in deps],
        "requirements": {
            "versions": [{"id": str(v.id), "version": v.version, "content_hash": v.content_hash,
                          "source": v.source, "created_by_kind": v.created_by_kind,
                          "provenance": v.provenance, "created_at": v.created_at.isoformat()}
                         for v in specs],
            "current": current.payload if current else None,
            "current_version": current.version if current else None,
            "approved_spec_id": str(task.approved_spec_id) if task.approved_spec_id else None,
            "open_blocking_questions": open_blocking_questions(current),
            "approval_scope_hash": req_scope[0] if req_scope else None,
        },
        "approvals": [{"id": str(a.id), "gate": a.gate, "decision": a.decision,
                       "scope_hash": a.scope_hash, "human_id": str(a.human_id or ""),
                       "reason": a.reason, "supersedes_id": str(a.supersedes_id or ""),
                       "created_at": a.created_at.isoformat()} for a in approvals],
        "permitted_actions": permitted_actions(ctx, task, project),
    }

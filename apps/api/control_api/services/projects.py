"""Project setup, agent configuration and activation (FR-04, FR-05, FR-06)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from control_api.db.models import (
    AgentConfig,
    Budget,
    Membership,
    Project,
    Repository,
    User,
    utcnow,
)
from control_api.domain.permissions import Action, AgentRole, HumanRole, can_create_project
from control_api.errors import Conflict, Forbidden, NotFound, Unprocessable
from control_api.services.audit import audit, record_denial
from control_api.services.context import Ctx
from control_api.services.outbox import emit

REQUIRED_BUDGET_SCOPES = ("PROJECT_MONTH", "TICKET", "RUN")


def project_snapshot(p: Project) -> dict[str, Any]:
    return {
        "id": str(p.id), "key": p.key, "name": p.name, "status": p.status,
        "classification": p.classification, "policy_version": p.policy_version,
        "version": p.version,
    }


def get_project(ctx: Ctx, project_id: uuid.UUID) -> Project:
    ctx.require(Action.PROJECT_READ, project_id, object_type="project", object_id=project_id)
    project = ctx.session.get(Project, project_id)
    if project is None:
        raise NotFound("project not found")
    return project


def list_projects(ctx: Ctx) -> list[Project]:
    ids = [uuid.UUID(pid) for pid in ctx.principal.project_roles]
    if not ids:
        return []
    return list(ctx.session.scalars(select(Project).where(Project.id.in_(ids)).order_by(Project.key)))


def create_project(
    ctx: Ctx, *, key: str, name: str, description: str, classification: str
) -> Project:
    decision = can_create_project(ctx.principal)
    if not decision.allowed:
        record_denial(ctx.principal, action="project.create", object_type="project",
                      object_id=None, project_id=None, correlation_id=ctx.correlation_id,
                      reason=decision.reason)
        raise Forbidden(decision.reason, code="permission_denied")
    project = Project(key=key, name=name, description=description, classification=classification)
    ctx.session.add(project)
    try:
        ctx.session.flush()
    except IntegrityError as exc:
        raise Conflict(f"project key {key!r} already exists", code="duplicate_key") from exc
    ctx.session.add(Membership(
        user_id=uuid.UUID(ctx.principal.id), project_id=project.id,
        roles=[HumanRole.ADMINISTRATOR.value],
    ))
    audit(ctx.session, ctx.principal, action="project.create", object_type="project",
          object_id=project.id, project_id=project.id, correlation_id=ctx.correlation_id,
          after=project_snapshot(project), policy_version=project.policy_version)
    emit(ctx.session, project_id=project.id, aggregate_type="project", aggregate_id=project.id,
         aggregate_version=project.version, type="project.created",
         payload=project_snapshot(project))
    return project


def set_membership(ctx: Ctx, project_id: uuid.UUID, *, subject: str, roles: list[HumanRole]) -> Membership:
    project = get_project(ctx, project_id)
    ctx.require(Action.PROJECT_MEMBERS_MANAGE, project.id, object_type="membership")
    user = ctx.session.scalar(select(User).where(User.subject == subject))
    if user is None:
        raise Unprocessable("unknown user subject", field_errors={"subject": "not found"})
    membership = ctx.session.scalar(select(Membership).where(
        Membership.user_id == user.id, Membership.project_id == project.id))
    before = {"roles": sorted(membership.roles)} if membership else None
    if membership is None:
        membership = Membership(user_id=user.id, project_id=project.id, roles=[])
        ctx.session.add(membership)
    membership.roles = sorted({r.value for r in roles})
    ctx.session.flush()
    audit(ctx.session, ctx.principal, action="project.members.set", object_type="membership",
          object_id=membership.id, project_id=project.id, correlation_id=ctx.correlation_id,
          before=before, after={"roles": membership.roles},
          details={"subject": subject, "roles": membership.roles},
          policy_version=project.policy_version)
    return membership


# ---------------------------------------------------------------- repository


class RepositoryInspector(Protocol):
    """Reads facts from GitHub through the Git broker. Never trusts user-supplied URLs."""

    def inspect(self, installation_id: int, repository_id: int, base_branch: str) -> dict[str, Any]:
        ...


class UnverifiedInspector:
    """Default until the GitHub App is connected: every fact is unknown, so activation blocks."""

    def inspect(self, installation_id: int, repository_id: int, base_branch: str) -> dict[str, Any]:
        return {
            "installation_access": None,
            "branch_exists": None,
            "branch_protected": None,
            "requires_up_to_date_checks": None,
            "required_checks": [],
            "owner": None,
            "name": None,
            "target_sha": None,
            "note": "GitHub App integration not configured; facts unverified",
        }


def configure_repository(
    ctx: Ctx, project_id: uuid.UUID, *, installation_id: int, repository_id: int,
    base_branch: str, inspector: RepositoryInspector,
) -> Repository:
    project = get_project(ctx, project_id)
    ctx.require(Action.REPOSITORY_CONFIGURE, project.id, object_type="repository")
    if project.status == "ACTIVE":
        raise Conflict("disable the project before changing its repository")
    facts = inspector.inspect(installation_id, repository_id, base_branch)
    repo = ctx.session.scalar(select(Repository).where(Repository.project_id == project.id))
    if repo is None:
        repo = Repository(project_id=project.id, github_id=repository_id,
                          installation_id=installation_id, owner="", name="", base_branch="")
        ctx.session.add(repo)
    repo.github_id = repository_id
    repo.installation_id = installation_id
    repo.base_branch = base_branch
    repo.owner = facts.get("owner") or ""
    repo.name = facts.get("name") or ""
    repo.verification = facts
    repo.verified_at = utcnow()
    ctx.session.flush()
    audit(ctx.session, ctx.principal, action="repository.configure", object_type="repository",
          object_id=repo.id, project_id=project.id, correlation_id=ctx.correlation_id,
          after={"github_id": repository_id, "installation_id": installation_id,
                 "base_branch": base_branch}, policy_version=project.policy_version)
    return repo


# ---------------------------------------------------------------- agents and budgets


def create_agent_config(ctx: Ctx, project_id: uuid.UUID, fields: dict[str, Any]) -> AgentConfig:
    project = get_project(ctx, project_id)
    ctx.require(Action.AGENT_CONFIGURE, project.id, object_type="agent_config")
    role = AgentRole(fields["role"])
    expected = fields.pop("expected_version", None)
    current = ctx.session.scalar(
        select(func.max(AgentConfig.version)).where(
            AgentConfig.project_id == project.id, AgentConfig.role == role.value)
    ) or 0
    if expected is not None and expected != current:
        raise Conflict(f"agent config for {role} is at version {current}, not {expected}",
                       code="stale_version")
    config = AgentConfig(project_id=project.id, version=current + 1,
                         created_by=uuid.UUID(ctx.principal.id), **{**fields, "role": role.value})
    ctx.session.add(config)
    try:
        ctx.session.flush()
    except IntegrityError as exc:
        raise Conflict("concurrent agent configuration change", code="stale_version") from exc
    audit(ctx.session, ctx.principal, action="agent_config.create", object_type="agent_config",
          object_id=config.id, project_id=project.id, correlation_id=ctx.correlation_id,
          after={"role": role.value, "version": config.version, "provider": config.provider,
                 "model": config.model, "adapter_version": config.adapter_version},
          policy_version=project.policy_version)
    return config


def set_budget(ctx: Ctx, project_id: uuid.UUID, *, scope: str, period: str, cap: Decimal) -> Budget:
    project = get_project(ctx, project_id)
    ctx.require(Action.BUDGET_CONFIGURE, project.id, object_type="budget")
    if scope not in REQUIRED_BUDGET_SCOPES:
        raise Unprocessable("unknown budget scope", field_errors={"scope": scope})
    if cap <= 0:
        raise Unprocessable("cap must be positive", field_errors={"cap": str(cap)})
    budget = ctx.session.scalar(select(Budget).where(
        Budget.project_id == project.id, Budget.scope == scope, Budget.period == period))
    before = {"cap": str(budget.cap)} if budget else None
    if budget is None:
        budget = Budget(project_id=project.id, scope=scope, period=period, cap=cap)
        ctx.session.add(budget)
    budget.cap = cap
    ctx.session.flush()
    audit(ctx.session, ctx.principal, action="budget.set", object_type="budget",
          object_id=budget.id, project_id=project.id, correlation_id=ctx.correlation_id,
          before=before, after={"scope": scope, "period": period, "cap": str(cap)},
          policy_version=project.policy_version)
    return budget


# ---------------------------------------------------------------- activation


@dataclass(frozen=True)
class ReadinessItem:
    key: str
    ok: bool
    detail: str


def activation_readiness(ctx: Ctx, project: Project) -> list[ReadinessItem]:
    s = ctx.session
    items: list[ReadinessItem] = []
    repo = s.scalar(select(Repository).where(Repository.project_id == project.id))
    facts = repo.verification if repo else {}

    def fact(key: str, label: str) -> None:
        value = facts.get(key)
        detail = "verified" if value is True else ("not verified" if value is None else "failed")
        items.append(ReadinessItem(key, value is True, f"{label}: {detail}"))

    items.append(ReadinessItem("repository_linked", repo is not None,
                               "repository linked" if repo else "no repository linked"))
    fact("installation_access", "GitHub App installation access")
    fact("branch_exists", "target branch exists")
    fact("branch_protected", "target branch protection")
    fact("requires_up_to_date_checks", "up-to-date checks or merge queue enforced")
    items.append(ReadinessItem(
        "required_checks", bool(facts.get("required_checks")),
        "required checks configured" if facts.get("required_checks")
        else "no required status checks on target branch"))
    items.append(ReadinessItem(
        "baseline_tests", facts.get("baseline_tests_passed") is True,
        "baseline tests passed in sandbox" if facts.get("baseline_tests_passed") is True
        else "baseline tests have not passed in sandbox"))

    configured = set(s.scalars(select(AgentConfig.role).where(
        AgentConfig.project_id == project.id)).all())
    for role in AgentRole:
        latest = s.scalar(select(AgentConfig).where(
            AgentConfig.project_id == project.id, AgentConfig.role == role.value
        ).order_by(AgentConfig.version.desc()).limit(1))
        passed = bool(latest and (latest.connection_test or {}).get("passed") is True)
        detail = (f"{role} agent not configured" if role.value not in configured
                  else f"{role} agent connection test " + ("passed" if passed else "not passed"))
        items.append(ReadinessItem(f"agent_{role.value.lower()}", passed, detail))

    scopes = set(s.scalars(select(Budget.scope).where(Budget.project_id == project.id)).all())
    for scope in REQUIRED_BUDGET_SCOPES:
        items.append(ReadinessItem(f"budget_{scope.lower()}", scope in scopes,
                                   f"{scope} budget " + ("set" if scope in scopes else "missing")))
    return items


def activate(
    ctx: Ctx, project_id: uuid.UUID, *, expected_version: int
) -> tuple[Project, list[ReadinessItem]]:
    project = ctx.session.scalar(select(Project).where(Project.id == project_id).with_for_update())
    if project is None:
        raise NotFound("project not found")
    ctx.require(Action.PROJECT_ACTIVATE, project.id, object_type="project", object_id=project.id)
    if project.version != expected_version:
        raise Conflict(f"project is at version {project.version}", code="stale_version")
    if project.status not in ("DRAFT", "DISABLED"):
        raise Conflict(f"cannot activate a project in status {project.status}")
    items = activation_readiness(ctx, project)
    missing = [i for i in items if not i.ok]
    if missing:
        record_denial(ctx.principal, action="project.activate", object_type="project",
                      object_id=project.id, project_id=project.id,
                      correlation_id=ctx.correlation_id,
                      reason="; ".join(i.detail for i in missing))
        raise Conflict("project is not ready for activation", code="activation_blocked",
                       details={"missing": [i.__dict__ for i in missing]})
    before = project_snapshot(project)
    project.status = "ACTIVE"
    project.version += 1
    audit(ctx.session, ctx.principal, action="project.activate", object_type="project",
          object_id=project.id, project_id=project.id, correlation_id=ctx.correlation_id,
          before=before, after=project_snapshot(project), policy_version=project.policy_version)
    emit(ctx.session, project_id=project.id, aggregate_type="project", aggregate_id=project.id,
         aggregate_version=project.version, type="project.activated",
         payload=project_snapshot(project))
    return project, items

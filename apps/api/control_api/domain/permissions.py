"""Role permissions (FRD FR-15, FR-16).

Deny by default. The matrix below is the single source of truth; UI hiding is
never a control. Approvals are only ever granted to active human principals —
agents and service identities are refused before the matrix is consulted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class PrincipalKind(StrEnum):
    HUMAN = "HUMAN"
    AGENT = "AGENT"
    SERVICE = "SERVICE"


class HumanRole(StrEnum):
    ADMINISTRATOR = "ADMINISTRATOR"
    PRODUCT_LEAD = "PRODUCT_LEAD"
    ENGINEERING_LEAD = "ENGINEERING_LEAD"
    QA_REVIEWER = "QA_REVIEWER"
    RELEASE_APPROVER = "RELEASE_APPROVER"
    MERGE_APPROVER = "MERGE_APPROVER"  # separate grant, e.g. for a Product Lead
    OBSERVER = "OBSERVER"


class AgentRole(StrEnum):
    BA = "BA"  # Gemini, optional Antigravity workspace
    DEVELOPER = "DEVELOPER"  # Claude Code
    QA = "QA"  # Codex
    JUNIOR = "JUNIOR"  # local Ollama


class Action(StrEnum):
    # Read
    PROJECT_READ = "project.read"
    TASK_READ = "task.read"
    AUDIT_READ = "audit.read"
    USAGE_READ = "usage.read"
    # Administration
    PROJECT_MEMBERS_MANAGE = "project.members.manage"
    PROJECT_ACTIVATE = "project.activate"
    POLICY_CHANGE = "policy.change"
    BUDGET_CONFIGURE = "budget.configure"
    AGENT_CONFIGURE = "agent.configure"
    SECRET_REFERENCE_MANAGE = "secret_ref.manage"
    # Engineering
    REPOSITORY_CONFIGURE = "repository.configure"
    COMMAND_POLICY_APPROVE = "command_policy.approve"
    # Tickets
    TASK_CREATE = "task.create"
    TASK_EDIT = "task.edit"
    TASK_ANALYZE = "task.analyze"
    TASK_CANCEL = "task.cancel"
    TASK_RESUME = "task.resume"
    TASK_RETRY = "task.retry"
    REQUIREMENTS_REQUEST_CHANGES = "requirements.request_changes"
    REQUIREMENTS_DRAFT_WRITE = "requirements.draft.write"
    CODE_REQUEST_CHANGES = "code.request_changes"
    # Gates — humans only
    APPROVE_REQUIREMENTS = "approve.requirements"
    APPROVE_MERGE = "approve.merge"
    APPROVE_RELEASE = "approve.release"
    RELEASE_RECORD_OUTCOME = "release.record_outcome"
    # Agent operations (broker-enforced)
    RUN_RESULT_SUBMIT = "run.result.submit"
    RUN_CLAIM = "run.claim"
    CONTEXT_READ = "context.read"
    FEATURE_WORKSPACE_WRITE = "workspace.feature.write"
    DEV_CHECKS_RUN = "dev_checks.run"
    QA_WORKSPACE_WRITE = "workspace.qa.write"
    CHILD_PATCH_WRITE = "workspace.child_patch.write"
    UX_ARTIFACT_WRITE = "ux_artifact.write"
    # Never granted to any machine identity
    GIT_PUSH_PROTECTED = "git.push.protected"
    MERGE_EXECUTE = "merge.execute"
    PRODUCTION_EXECUTE = "production.execute"


GATE_ACTIONS = frozenset({Action.APPROVE_REQUIREMENTS, Action.APPROVE_MERGE, Action.APPROVE_RELEASE})

_READ = {Action.PROJECT_READ, Action.TASK_READ, Action.AUDIT_READ, Action.USAGE_READ}

HUMAN_GRANTS: dict[HumanRole, frozenset[Action]] = {
    HumanRole.ADMINISTRATOR: frozenset(_READ | {
        Action.PROJECT_MEMBERS_MANAGE, Action.PROJECT_ACTIVATE, Action.POLICY_CHANGE,
        Action.BUDGET_CONFIGURE, Action.AGENT_CONFIGURE, Action.SECRET_REFERENCE_MANAGE,
        Action.TASK_CANCEL, Action.TASK_RESUME, Action.TASK_RETRY,
    }),
    HumanRole.PRODUCT_LEAD: frozenset(_READ | {
        Action.TASK_CREATE, Action.TASK_EDIT, Action.TASK_ANALYZE, Action.TASK_CANCEL,
        Action.TASK_RESUME, Action.TASK_RETRY, Action.REQUIREMENTS_REQUEST_CHANGES,
        Action.REQUIREMENTS_DRAFT_WRITE, Action.APPROVE_REQUIREMENTS,
    }),
    HumanRole.ENGINEERING_LEAD: frozenset(_READ | {
        Action.REPOSITORY_CONFIGURE, Action.COMMAND_POLICY_APPROVE, Action.AGENT_CONFIGURE,
        Action.TASK_CREATE, Action.TASK_CANCEL, Action.TASK_RESUME, Action.TASK_RETRY,
        Action.CODE_REQUEST_CHANGES, Action.APPROVE_MERGE,
    }),
    HumanRole.QA_REVIEWER: frozenset(_READ | {Action.TASK_RETRY}),
    HumanRole.RELEASE_APPROVER: frozenset(_READ | {
        Action.APPROVE_RELEASE, Action.RELEASE_RECORD_OUTCOME,
    }),
    HumanRole.MERGE_APPROVER: frozenset({Action.APPROVE_MERGE, Action.CODE_REQUEST_CHANGES}),
    HumanRole.OBSERVER: frozenset({Action.PROJECT_READ, Action.TASK_READ}),
}

AGENT_GRANTS: dict[AgentRole, frozenset[Action]] = {
    AgentRole.BA: frozenset({
        Action.CONTEXT_READ, Action.REQUIREMENTS_DRAFT_WRITE, Action.UX_ARTIFACT_WRITE,
        Action.RUN_RESULT_SUBMIT,
    }),
    AgentRole.DEVELOPER: frozenset({
        Action.CONTEXT_READ, Action.FEATURE_WORKSPACE_WRITE, Action.DEV_CHECKS_RUN,
        Action.UX_ARTIFACT_WRITE, Action.RUN_RESULT_SUBMIT,
    }),
    AgentRole.QA: frozenset({
        Action.CONTEXT_READ, Action.QA_WORKSPACE_WRITE, Action.RUN_RESULT_SUBMIT,
    }),
    AgentRole.JUNIOR: frozenset({Action.CHILD_PATCH_WRITE, Action.RUN_RESULT_SUBMIT}),
}

# Workers relay agent output; the API re-authorizes it as the agent role that produced it.
SERVICE_GRANTS: frozenset[Action] = frozenset({Action.RUN_RESULT_SUBMIT, Action.RUN_CLAIM})

# Actions no machine identity may ever perform, regardless of configuration.
MACHINE_FORBIDDEN = frozenset(
    GATE_ACTIONS | {
        Action.GIT_PUSH_PROTECTED, Action.MERGE_EXECUTE, Action.PRODUCTION_EXECUTE,
        Action.POLICY_CHANGE, Action.PROJECT_MEMBERS_MANAGE, Action.SECRET_REFERENCE_MANAGE,
    }
)


@dataclass(frozen=True)
class Principal:
    kind: PrincipalKind
    id: str
    active: bool = True
    workspace_admin: bool = False
    # project_id -> roles held in that project (humans only)
    project_roles: dict[str, frozenset[HumanRole]] = field(default_factory=dict)
    # machine identities are scoped to one project and one role
    agent_role: AgentRole | None = None
    scoped_project_id: str | None = None

    @property
    def is_human(self) -> bool:
        return self.kind is PrincipalKind.HUMAN

    def roles_in(self, project_id: str) -> frozenset[HumanRole]:
        return self.project_roles.get(project_id, frozenset())

    def is_member(self, project_id: str) -> bool:
        if self.is_human:
            return bool(self.roles_in(project_id))
        return self.scoped_project_id == project_id


@dataclass(frozen=True)
class AuthzDecision:
    allowed: bool
    reason: str


def authorize(principal: Principal, action: Action, project_id: str | None) -> AuthzDecision:
    if not principal.active:
        return AuthzDecision(False, "principal is inactive")

    if not principal.is_human:
        if action in MACHINE_FORBIDDEN:
            return AuthzDecision(False, f"{principal.kind} identities can never perform {action}")
        if project_id is None or principal.scoped_project_id != project_id:
            return AuthzDecision(False, "machine identity is not scoped to this project")
        grants = (
            AGENT_GRANTS.get(principal.agent_role, frozenset())
            if principal.kind is PrincipalKind.AGENT and principal.agent_role
            else SERVICE_GRANTS
        )
        if action in grants:
            return AuthzDecision(True, f"granted to {principal.agent_role or principal.kind}")
        return AuthzDecision(False, f"{principal.agent_role or principal.kind} lacks {action}")

    if project_id is None:
        return AuthzDecision(False, f"{action} requires a project scope")
    roles = principal.roles_in(project_id)
    if not roles:
        return AuthzDecision(False, "not a member of this project")
    for role in roles:
        if action in HUMAN_GRANTS[role]:
            return AuthzDecision(True, f"granted by {role}")
    held = ", ".join(sorted(r.value for r in roles))
    return AuthzDecision(False, f"role {held} does not grant {action}")


def can_create_project(principal: Principal) -> AuthzDecision:
    if principal.is_human and principal.active and principal.workspace_admin:
        return AuthzDecision(True, "workspace administrator")
    return AuthzDecision(False, "only workspace administrators can create projects")

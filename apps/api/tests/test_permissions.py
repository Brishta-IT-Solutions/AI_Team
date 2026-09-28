"""FR-15/16 permission matrix — deny by default, machines never approve (AT-04, AT-19)."""

import pytest

from control_api.domain.permissions import (
    GATE_ACTIONS,
    MACHINE_FORBIDDEN,
    Action,
    AgentRole,
    HumanRole,
    Principal,
    PrincipalKind,
    authorize,
)

P = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"


def human(*roles: HumanRole, active: bool = True) -> Principal:
    return Principal(kind=PrincipalKind.HUMAN, id="u", active=active,
                     project_roles={P: frozenset(roles)})


def agent(role: AgentRole, project: str = P) -> Principal:
    return Principal(kind=PrincipalKind.AGENT, id=f"agent:{role}", agent_role=role,
                     scoped_project_id=project)


@pytest.mark.parametrize("role", list(AgentRole))
@pytest.mark.parametrize("action", sorted(MACHINE_FORBIDDEN))
def test_agents_can_never_perform_forbidden_actions(role, action):
    assert not authorize(agent(role), action, P).allowed


def test_service_identities_can_never_approve():
    svc = Principal(kind=PrincipalKind.SERVICE, id="service:x", scoped_project_id=P)
    for action in GATE_ACTIONS:
        assert not authorize(svc, action, P).allowed


@pytest.mark.parametrize(("role", "forbidden"), [
    (AgentRole.BA, Action.FEATURE_WORKSPACE_WRITE),
    (AgentRole.BA, Action.QA_WORKSPACE_WRITE),
    (AgentRole.DEVELOPER, Action.REQUIREMENTS_DRAFT_WRITE),  # no approved-spec edits
    (AgentRole.DEVELOPER, Action.QA_WORKSPACE_WRITE),
    (AgentRole.QA, Action.FEATURE_WORKSPACE_WRITE),  # no production-code fixes
    (AgentRole.QA, Action.REQUIREMENTS_DRAFT_WRITE),
    (AgentRole.JUNIOR, Action.FEATURE_WORKSPACE_WRITE),
    (AgentRole.JUNIOR, Action.CONTEXT_READ),
])
def test_agents_are_confined_to_their_role(role, forbidden):
    assert not authorize(agent(role), forbidden, P).allowed


def test_agent_scope_is_bound_to_one_project():
    assert authorize(agent(AgentRole.BA), Action.REQUIREMENTS_DRAFT_WRITE, P).allowed
    assert not authorize(agent(AgentRole.BA), Action.REQUIREMENTS_DRAFT_WRITE, OTHER).allowed


def test_role_separation_for_gates():
    assert authorize(human(HumanRole.PRODUCT_LEAD), Action.APPROVE_REQUIREMENTS, P).allowed
    assert not authorize(human(HumanRole.PRODUCT_LEAD), Action.APPROVE_MERGE, P).allowed
    assert authorize(human(HumanRole.PRODUCT_LEAD, HumanRole.MERGE_APPROVER),
                     Action.APPROVE_MERGE, P).allowed
    assert authorize(human(HumanRole.ENGINEERING_LEAD), Action.APPROVE_MERGE, P).allowed
    assert not authorize(human(HumanRole.ENGINEERING_LEAD), Action.APPROVE_RELEASE, P).allowed
    assert not authorize(human(HumanRole.ADMINISTRATOR), Action.APPROVE_REQUIREMENTS, P).allowed
    assert not authorize(human(HumanRole.ADMINISTRATOR), Action.APPROVE_MERGE, P).allowed


def test_observer_is_read_only():
    obs = human(HumanRole.OBSERVER)
    mutations = [a for a in Action if a not in (Action.PROJECT_READ, Action.TASK_READ)]
    assert authorize(obs, Action.TASK_READ, P).allowed
    assert not any(authorize(obs, a, P).allowed for a in mutations)


def test_non_member_and_inactive_are_denied():
    assert not authorize(human(HumanRole.PRODUCT_LEAD), Action.TASK_READ, OTHER).allowed
    assert not authorize(human(HumanRole.PRODUCT_LEAD, active=False), Action.TASK_READ, P).allowed


def test_humans_never_hold_machine_only_actions():
    everyone = human(*HumanRole)
    for action in (Action.GIT_PUSH_PROTECTED, Action.MERGE_EXECUTE, Action.PRODUCTION_EXECUTE):
        assert not authorize(everyone, action, P).allowed


def test_only_humans_delete_tickets():
    assert authorize(human(HumanRole.PRODUCT_LEAD), Action.TASK_DELETE, P).allowed
    assert authorize(human(HumanRole.ADMINISTRATOR), Action.TASK_DELETE, P).allowed
    assert not authorize(human(HumanRole.ENGINEERING_LEAD), Action.TASK_DELETE, P).allowed
    assert Action.TASK_DELETE in MACHINE_FORBIDDEN

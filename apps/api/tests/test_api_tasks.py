"""Ticket journey through the requirements gate, over HTTP."""

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from control_api.db.models import Approval, AuditEvent, RequirementVersion
from tests.conftest import ba_spec


@pytest.fixture
def task(api, project):
    r = api.post("product", f"/projects/{project['id']}/tasks",
                 {"title": "Expatriate sign-in", "priority": "P1",
                  "description": "National identity sign-in for expatriates"})
    assert r.status_code == 201, r.json()
    return r.json()


def view(api, tid, who="product"):
    return api.get(who, f"/tasks/{tid}").json()


def command(api, tid, cmd, who="product", **kw):
    version = view(api, tid)["task"]["version"]
    return api.post(who, f"/tasks/{tid}/commands",
                    {"command": cmd, "expected_version": version, **kw})


def submit(api, tid, spec, who="product", source="HUMAN_EDIT"):
    version = view(api, tid)["task"]["version"]
    return api.post(who, f"/tasks/{tid}/requirements",
                    {"expected_version": version, "payload": spec, "source": source})


def approve_requirements(api, tid, who="product", scope_hash=None):
    v = view(api, tid)
    return api.post(who, f"/tasks/{tid}/approvals", {
        "gate": "REQUIREMENTS", "decision": "APPROVED", "expected_version": v["task"]["version"],
        "scope_hash": scope_hash or v["requirements"]["approval_scope_hash"]})


def test_ticket_keys_and_validation(api, project, task):
    assert task["key"] == "PORTAL-1" and task["stage"] == "NEW" and task["column"] == "Backlog"
    bad = api.post("product", f"/projects/{project['id']}/tasks", {"title": "", "priority": "P9"})
    assert bad.status_code == 400 and {"body.title", "body.priority"} <= set(bad.json()["field_errors"])
    dep = api.post("product", f"/projects/{project['id']}/tasks",
                   {"title": "dep", "priority": "P2",
                    "dependencies": ["00000000-0000-0000-0000-000000000001"]})
    assert dep.status_code == 422


def test_happy_path_approval_hands_work_to_developer(api, task):
    tid = task["id"]
    assert command(api, tid, "analyze").json()["task"]["stage"] == "BA_ANALYSIS"
    r = submit(api, tid, ba_spec())
    assert r.status_code == 201 and r.json()["task"]["stage"] == "REQUIREMENTS_APPROVAL"
    r = approve_requirements(api, tid)
    assert r.status_code == 200, r.json()
    v = view(api, tid)
    # Approval reserves budget, takes the branch lease and queues Claude Code.
    assert v["task"]["stage"] == "DEVELOPING" and v["task"]["execution_status"] == "QUEUED"
    assert v["requirements"]["approved_spec_id"] == v["task"]["current_spec_id"]
    assert [a["decision"] for a in v["approvals"]] == ["APPROVED"]


def test_blocking_question_prevents_approval(api, task):  # AT-02
    tid = task["id"]
    command(api, tid, "analyze")
    submit(api, tid, ba_spec(blocking_open=True))
    r = approve_requirements(api, tid)
    assert r.status_code == 409 and "blocking question" in r.json()["message"]
    v = view(api, tid)
    assert v["task"]["stage"] == "REQUIREMENTS_APPROVAL" and v["approvals"] == []
    allowed = {a["command"]: a for a in v["permitted_actions"]}
    assert not allowed["approve_requirements"]["allowed"]
    assert any("blocking" in r for r in allowed["approve_requirements"]["reasons"])


def test_stale_scope_is_rejected(api, task):
    tid = task["id"]
    command(api, tid, "analyze")
    submit(api, tid, ba_spec())
    old_hash = view(api, tid)["requirements"]["approval_scope_hash"]
    submit(api, tid, ba_spec(extra_ac=True))  # newer draft replaces the one under review
    r = approve_requirements(api, tid, scope_hash=old_hash)
    assert r.status_code == 409 and r.json()["code"] == "stale_scope"


def test_editing_approved_requirements_revokes_and_versions(api, task, db):  # AT-03
    tid = task["id"]
    command(api, tid, "analyze")
    submit(api, tid, ba_spec())
    approve_requirements(api, tid)
    r = submit(api, tid, ba_spec(extra_ac=True))
    assert r.status_code == 201 and r.json()["version"] == 2
    v = view(api, tid)
    assert v["task"]["stage"] == "REQUIREMENTS_APPROVAL"
    assert v["requirements"]["approved_spec_id"] is None
    assert [s["version"] for s in v["requirements"]["versions"]] == [1, 2]
    assert [a["decision"] for a in v["approvals"]] == ["APPROVED", "REVOKED"]
    assert db.scalar(select(RequirementVersion).where(RequirementVersion.version == 1)) is not None


def test_identical_resubmission_is_a_noop(api, task):
    tid = task["id"]
    command(api, tid, "analyze")
    submit(api, tid, ba_spec())
    r = submit(api, tid, ba_spec())
    assert r.status_code == 200 and r.json()["created"] is False


def test_agents_cannot_approve_and_denials_are_audited(api, project, task, db):  # AT-04
    tid = task["id"]
    command(api, tid, "analyze")
    submit(api, tid, ba_spec())
    v = view(api, tid)
    for role in ("BA", "DEVELOPER", "QA", "JUNIOR"):
        token = f"dev-agent:{role}:{project['id']}:run-1"
        r = api.post(token, f"/tasks/{tid}/approvals", {
            "gate": "REQUIREMENTS", "decision": "APPROVED",
            "expected_version": v["task"]["version"],
            "scope_hash": v["requirements"]["approval_scope_hash"]})
        assert r.status_code == 403, role
    denials = db.scalars(select(AuditEvent).where(AuditEvent.outcome == "DENIED",
                                                  AuditEvent.actor_kind == "AGENT")).all()
    assert len(denials) == 4
    assert view(api, tid)["approvals"] == []


def test_ba_agent_may_draft_only_during_analysis(api, project, task):
    tid = task["id"]
    token = f"dev-agent:BA:{project['id']}:run-1"
    assert submit(api, tid, ba_spec(), who=token, source="AGENT").status_code == 409  # NEW
    command(api, tid, "analyze")
    assert submit(api, tid, ba_spec(), who=token, source="AGENT").status_code == 201
    dev = f"dev-agent:DEVELOPER:{project['id']}:run-2"
    assert submit(api, tid, ba_spec(extra_ac=True), who=dev).status_code == 403


def test_invalid_ba_payload_is_422(api, task):
    tid = task["id"]
    command(api, tid, "analyze")
    spec = ba_spec()
    spec["acceptance_criteria"] = []
    r = submit(api, tid, spec)
    assert r.status_code == 422 and r.json()["code"] == "schema_invalid"


def test_manual_import_records_provenance(api, task):  # AT-23 (import path)
    tid = task["id"]
    command(api, tid, "analyze")
    version = view(api, tid)["task"]["version"]
    r = api.post("product", f"/tasks/{tid}/requirements", {
        "expected_version": version, "payload": ba_spec(), "source": "MANUAL_IMPORT",
        "provenance": {"tool": "antigravity", "exported_at": "2026-09-28T10:00:00Z"}})
    assert r.status_code == 201
    versions = view(api, tid)["requirements"]["versions"]
    assert versions[0]["source"] == "MANUAL_IMPORT"
    assert versions[0]["provenance"]["tool"] == "antigravity"


def test_observer_cannot_mutate(api, task):
    r = command(api, task["id"], "analyze", who="observer")
    assert r.status_code == 403
    assert view(api, task["id"], who="observer")["task"]["stage"] == "NEW"


def test_illegal_jump_leaves_stage_unchanged(api, task):  # AT-17
    r = approve_requirements(api, task["id"], scope_hash="0" * 64)
    assert r.status_code == 409
    assert view(api, task["id"])["task"]["stage"] == "NEW"


def test_stale_expected_version_is_409(api, task):
    r = api.post("product", f"/tasks/{task['id']}/commands",
                 {"command": "analyze", "expected_version": 99})
    assert r.status_code == 409 and r.json()["code"] == "stale_version"


def test_duplicate_command_is_applied_once(api, task, db):  # AT-05 (command side)
    body = {"command": "analyze", "expected_version": task["version"]}
    a = api.post("product", f"/tasks/{task['id']}/commands", body, key="start-1")
    b = api.post("product", f"/tasks/{task['id']}/commands", body, key="start-1")
    assert a.status_code == b.status_code == 202 and a.json() == b.json()
    transitions = db.scalars(select(AuditEvent).where(
        AuditEvent.action == "task.transition.start_analysis")).all()
    assert len(transitions) == 1


def test_request_changes_requires_reason_then_returns_to_analysis(api, task):
    tid = task["id"]
    command(api, tid, "analyze")
    submit(api, tid, ba_spec())
    assert command(api, tid, "request_changes").status_code == 409
    r = command(api, tid, "request_changes", reason="Clarify identity tenants")
    assert r.status_code == 202 and r.json()["task"]["stage"] == "BA_ANALYSIS"
    assert view(api, tid)["approvals"][-1]["decision"] == "CHANGES_REQUESTED"


def test_cancel_is_terminal_and_preserves_evidence(api, task):
    tid = task["id"]
    command(api, tid, "analyze")
    submit(api, tid, ba_spec())
    assert command(api, tid, "cancel").status_code == 409  # reason required
    assert command(api, tid, "cancel", reason="Superseded").json()["task"]["stage"] == "CANCELLED"
    v = view(api, tid)
    assert len(v["requirements"]["versions"]) == 1
    assert command(api, tid, "analyze").status_code == 409
    assert command(api, tid, "retry", reason="x").status_code == 409


def test_event_cursor_catch_up_has_no_duplicates(api, project, task):  # AT-18
    pid, tid = project["id"], task["id"]
    first = api.get("product", f"/projects/{pid}/events", limit=2).json()
    command(api, tid, "analyze")
    submit(api, tid, ba_spec())
    seen = [e["event_id"] for e in first["items"]]
    cursor = first["next_cursor"]
    while True:
        page = api.get("product", f"/projects/{pid}/events", after=cursor, limit=2).json()
        if not page["items"]:
            break
        seen += [e["event_id"] for e in page["items"]]
        cursor = page["next_cursor"]
    assert len(seen) == len(set(seen))
    everything = api.get("product", f"/projects/{pid}/events", limit=100).json()["items"]
    assert seen == [e["event_id"] for e in everything]
    assert everything[-1]["type"] == "requirements.version_created"


def test_audit_and_approvals_are_append_only(api, task, db):  # NFR-07
    tid = task["id"]
    command(api, tid, "analyze")
    submit(api, tid, ba_spec())
    approve_requirements(api, tid)
    for stmt in ("UPDATE audit_events SET reason = 'x'", "DELETE FROM audit_events",
                 "UPDATE approvals SET decision = 'REJECTED'", "DELETE FROM approvals",
                 "UPDATE requirement_versions SET payload = '{}'"):
        with pytest.raises(DBAPIError, match="append-only"):
            db.execute(text(stmt))
        db.rollback()
    assert db.scalar(select(Approval)) is not None


def test_every_mutation_has_an_audit_event(api, task, db):  # NFR-07 reconciliation
    tid = task["id"]
    command(api, tid, "analyze")
    submit(api, tid, ba_spec())
    approve_requirements(api, tid)
    actions = [a for (a,) in db.execute(select(AuditEvent.action).order_by(AuditEvent.seq))]
    for expected in ("task.create", "task.transition.start_analysis",
                     "requirements.version.create", "task.transition.ba_result_valid",
                     "approval.requirements.approved",
                     "task.transition.approve_requirements"):
        assert expected in actions


def test_board_groups_by_column(api, project, task):
    command(api, task["id"], "analyze")
    board = api.get("observer", f"/projects/{project['id']}/board").json()
    columns = {c["name"]: [i["key"] for i in c["items"]] for c in board["columns"]}
    assert list(columns) == ["Backlog", "Analysis", "Approval", "Development", "QA", "Merge",
                             "Done"]
    assert columns["Analysis"] == ["PORTAL-1"]


def test_task_list_cursor_pagination(api, project):
    for i in range(5):
        api.post("product", f"/projects/{project['id']}/tasks", {"title": f"t{i}",
                                                                   "priority": "P2"})
    page1 = api.get("product", f"/projects/{project['id']}/tasks", limit=3).json()
    page2 = api.get("product", f"/projects/{project['id']}/tasks", limit=3,
                    cursor=page1["next_cursor"]).json()
    keys = [t["key"] for t in page1["items"] + page2["items"]]
    assert len(keys) == 5 == len(set(keys)) and page2["next_cursor"] is None


def test_permitted_actions_separate_role_from_state(api, task):
    observer = {a["command"]: a for a in view(api, task["id"], who="observer")["permitted_actions"]}
    assert not any(a["authorized"] for a in observer.values())
    assert observer["cancel"]["reasons"] == ["role OBSERVER does not grant task.cancel"]
    product = {a["command"]: a for a in view(api, task["id"])["permitted_actions"]}
    assert product["analyze"]["allowed"] and product["analyze"]["authorized"]
    assert product["approve_requirements"]["authorized"] and not product["approve_requirements"]["allowed"]
    assert not product["approve_merge"]["authorized"]


def test_delete_hides_ticket_but_keeps_history(api, project, task, db):
    tid, pid = task["id"], project["id"]
    command(api, tid, "analyze")
    submit(api, tid, ba_spec())
    blocked = {a["command"]: a for a in view(api, tid)["permitted_actions"]}["delete"]
    assert blocked["authorized"] and not blocked["allowed"]
    assert "cancel PORTAL-1 first" in blocked["reasons"][0]
    r = command(api, tid, "delete")
    assert r.status_code == 409 and r.json()["details"]["unmet"] == blocked["reasons"]

    command(api, tid, "cancel", reason="Old demo ticket")
    assert command(api, tid, "delete", reason="Cleaning up").status_code == 202
    assert api.get("product", f"/tasks/{tid}").status_code == 404
    board = api.get("product", f"/projects/{pid}/board").json()
    assert not any(c["items"] for c in board["columns"])
    assert api.get("product", f"/projects/{pid}/tasks").json()["items"] == []
    # The history stays: spec versions and the audit trail are append-only.
    assert db.scalar(select(RequirementVersion.version).where(RequirementVersion.task_id == tid)) == 1
    deleted = db.scalar(select(AuditEvent).where(AuditEvent.action == "task.delete"))
    assert deleted.reason == "Cleaning up" and deleted.actor_id


def test_delete_new_ticket_and_dependents_block(api, project, task):
    pid, tid = project["id"], task["id"]
    dep = api.post("product", f"/projects/{pid}/tasks",
                   {"title": "Follow-up", "priority": "P2", "dependencies": [tid]}).json()
    r = command(api, tid, "delete")
    assert r.status_code == 409 and r.json()["details"]["unmet"] == ["PORTAL-2 still depend on PORTAL-1"]
    assert command(api, dep["id"], "delete").status_code == 202  # a NEW ticket, nothing depends on it
    assert command(api, tid, "delete").status_code == 202
    # A deleted ticket can't be picked as a new dependency.
    r = api.post("product", f"/projects/{pid}/tasks", {"title": "x", "priority": "P2", "dependencies": [tid]})
    assert r.status_code == 422


def test_only_leads_and_admins_delete(api, task):
    assert command(api, task["id"], "delete", who="observer").status_code == 403
    assert command(api, task["id"], "delete", who="eng").status_code == 403
    assert command(api, task["id"], "delete", who="admin").status_code == 202

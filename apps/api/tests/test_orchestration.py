"""The team loop over HTTP: BA → approval → developer → self-check → QA → repair → QA pass.

A simulated worker plays each team member; the API must hold every rule regardless of what
the worker sends.
"""

import hashlib
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from control_api.db.models import AuditEvent, BranchLease, Defect, Run
from tests.conftest import ba_spec

WORKER = "dev-service:worker:PORTAL"
BASE, HEAD1, HEAD2 = "b" * 40, "1" * 40, "2" * 40
REF = "e" * 64


@pytest.fixture
def policy(api, project):
    r = api.put("eng", f"/projects/{project['id']}/execution-policy", {
        "commands": [{"id": "test", "argv": ["npm", "test"]}],
        "protected_paths": ["infra/*"], "expected_version": project["version"]})
    assert r.status_code == 200, r.json()
    return r.json()


@pytest.fixture
def ticket(api, project, policy):
    t = api.post("product", f"/projects/{project['id']}/tasks",
                 {"title": "Expatriate sign-in", "priority": "P1"}).json()
    return t


def view(api, tid, who="product"):
    return api.get(who, f"/tasks/{tid}").json()


def command(api, tid, cmd, **kw):
    return api.post("product", f"/tasks/{tid}/commands",
                    {"command": cmd, "expected_version": view(api, tid)["task"]["version"], **kw})


def claim(api, role, worker_id="w1"):
    api.post(WORKER, "/workers/hello", {"worker_id": worker_id, "capabilities": {
        role: {"available": True, "provider": "test", "model": "test-model"}}})
    r = api.post(WORKER, "/workers/claim", {"worker_id": worker_id, "roles": [role]})
    assert r.status_code == 200, r.json()
    return r.json()["run"]


def result(api, run, payload, outcome="SUCCEEDED", usage=None):
    return api.post(WORKER, f"/runs/{run['run_id']}/results", {
        "claim_token": run["claim_token"], "outcome": outcome, "payload": payload,
        "usage": usage or {"input_tokens": 10, "output_tokens": 5, "cost_usd": "0.05",
                           "quality": "PROVIDER_ESTIMATE"},
        "provider": "test", "model": "test-model"})


def submission(head, *, exit_code=0, files=("src/signin.ts",)):
    return {"submission": {"head_sha": head, "base_sha": BASE, "files_changed": list(files),
                           "summary": "Implemented sign-in", "tests": [
                               {"command_id": "test", "exit_code": exit_code, "artifact_ref": REF}]}}


def qa_report(head, results, findings=()):
    return {"spec_version": 1, "head_sha": head, "base_sha": BASE,
            "criteria_results": [{"ac_id": k, "result": v, "evidence_refs": [REF]} for k, v in results.items()],
            "suites": [{"name": "test", "mandatory": True, "result": "PASS"}],
            "findings": list(findings),
            "overall_result": "FAIL" if "FAIL" in results.values() else "PASS"}


def approve(api, tid):
    v = view(api, tid)
    r = api.post("product", f"/tasks/{tid}/approvals", {
        "gate": "REQUIREMENTS", "decision": "APPROVED", "expected_version": v["task"]["version"],
        "scope_hash": v["requirements"]["approval_scope_hash"]})
    assert r.status_code == 200, r.json()


def to_developing(api, tid):
    command(api, tid, "analyze")
    ba = claim(api, "BA")
    assert result(api, ba, ba_spec()).status_code == 200
    approve(api, tid)
    return claim(api, "DEVELOPER")


def test_full_team_loop_with_one_repair(api, ticket, db):  # AT-06
    tid = ticket["id"]
    command(api, tid, "analyze")
    assert view(api, tid)["task"]["execution_status"] == "QUEUED"

    ba = claim(api, "BA")
    assert "Expatriate sign-in" in ba["envelope"]["prompt"]
    assert result(api, ba, ba_spec()).json()["spec_version"] == 1
    v = view(api, tid)
    assert v["task"]["stage"] == "REQUIREMENTS_APPROVAL"
    assert v["requirements"]["versions"][0]["source"] == "AGENT"

    approve(api, tid)
    dev = claim(api, "DEVELOPER")
    env = dev["envelope"]
    assert env["spec"]["goal"] and env["branch"].startswith("feature/portal-1/") and env["lease_token"]
    assert view(api, tid)["task"]["execution_status"] == "RUNNING"

    # First candidate: self-check fails → FIX_REQUIRED → repair dispatched automatically.
    out = result(api, dev, submission(HEAD1, exit_code=1)).json()
    assert out["review"] == "FAILED" and "exit code 1" in out["findings"][0]
    repair = claim(api, "DEVELOPER")
    assert any("exit code 1" in f for f in repair["envelope"]["repair_findings"])

    # Second candidate passes self-check → QA.
    assert result(api, repair, submission(HEAD1)).json()["review"] == "PASSED"
    qa = claim(api, "QA")
    assert qa["envelope"]["mandatory_ac_ids"] == ["AC-1", "AC-4"]
    assert "reasoning" in qa["envelope"]["prompt"]  # QA is told it gets no developer reasoning
    assert "submission" not in str(qa["envelope"])

    # QA fails AC-4 with reproduction evidence → defect + FIX_REQUIRED → repair with the defect.
    finding = {"ac_id": "AC-4", "severity": "HIGH", "title": "Expired identity is accepted",
               "reproduction_steps": ["Sign in with an expired ID"], "expected": "Refused", "actual": "Signed in"}
    out = result(api, qa, qa_report(HEAD1, {"AC-1": "PASS", "AC-4": "FAIL"}, [finding])).json()
    assert out["gate"] == "FAIL"
    defect = db.scalar(select(Defect))
    assert defect.ac_id == "AC-4" and defect.status == "OPEN"
    fix = claim(api, "DEVELOPER")
    assert fix["envelope"]["repair_cycle"] is True
    reviews = [r["result"]["review"] for r in api.get("product", f"/tasks/{tid}/runs").json()["items"]
               if r["role"] == "DEVELOPER" and r["result"]]
    assert reviews == ["PASSED", "FAILED"]  # persisted, newest first
    assert fix["envelope"]["defects"][0]["title"] == "Expired identity is accepted"

    # Repaired commit reaches QA (one counted cycle) and passes a full retest.
    assert result(api, fix, submission(HEAD2)).json()["review"] == "PASSED"
    assert view(api, tid)["task"]["repair_count"] == 1
    retest = claim(api, "QA")
    assert retest["envelope"]["head_sha"] == HEAD2
    assert result(api, retest, qa_report(HEAD2, {"AC-1": "PASS", "AC-4": "PASS"})).json()["gate"] == "PASS"

    v = view(api, tid)
    assert v["task"]["stage"] == "MERGE_APPROVAL"
    db.expire_all()
    assert db.scalar(select(Defect)).status == "RESOLVED"
    merge = next(a for a in v["permitted_actions"] if a["command"] == "approve_merge")
    assert not merge["allowed"] and any("Git broker" in r for r in merge["reasons"])


def test_manual_import_supersedes_queued_ba(api, ticket, db):  # AT-23
    tid = ticket["id"]
    command(api, tid, "analyze")
    r = api.post("product", f"/tasks/{tid}/requirements", {
        "expected_version": view(api, tid)["task"]["version"], "payload": ba_spec(),
        "source": "MANUAL_IMPORT", "provenance": {"tool": "antigravity"}})
    assert r.status_code == 201
    assert db.scalar(select(Run)).status == "CANCELLED"
    assert api.post(WORKER, "/workers/claim", {"worker_id": "w1", "roles": ["BA"]}).json()["run"] is None


def test_ba_brief_for_antigravity(api, ticket):
    brief = api.get("product", f"/tasks/{ticket['id']}/ba-brief").json()["markdown"]
    assert "Antigravity" in brief and "Expatriate sign-in" in brief and '"acceptance_criteria"' in brief


def test_invalid_ba_result_is_rejected(api, ticket):
    command(api, ticket["id"], "analyze")
    ba = claim(api, "BA")
    bad = ba_spec()
    bad["acceptance_criteria"] = []
    out = result(api, ba, bad).json()
    assert out["applied"] is False
    v = view(api, ticket["id"])
    assert v["task"]["stage"] == "BA_ANALYSIS" and v["task"]["execution_status"] == "FAILED"
    assert command(api, ticket["id"], "retry", reason="try again").status_code == 202
    assert claim(api, "BA")["attempt"] == 2


def test_incomplete_qa_report_never_reaches_merge_approval(api, ticket):  # AT-07
    tid = ticket["id"]
    dev = to_developing(api, tid)
    result(api, dev, submission(HEAD1))
    qa = claim(api, "QA")
    out = result(api, qa, qa_report(HEAD1, {"AC-1": "PASS"})).json()
    assert out["applied"] is False and "missing criteria: AC-4" in out["reason"]
    v = view(api, tid)
    assert v["task"]["stage"] == "QA" and v["task"]["execution_status"] == "BLOCKED"


def test_qa_on_wrong_commit_is_rejected(api, ticket):
    dev = to_developing(api, ticket["id"])
    result(api, dev, submission(HEAD1))
    qa = claim(api, "QA")
    out = result(api, qa, qa_report(HEAD2, {"AC-1": "PASS", "AC-4": "PASS"})).json()
    assert out["applied"] is False and "head SHA" in out["reason"]


def test_blocked_suite_is_not_a_pass(api, ticket):
    dev = to_developing(api, ticket["id"])
    result(api, dev, submission(HEAD1))
    qa = claim(api, "QA")
    report = qa_report(HEAD1, {"AC-1": "PASS", "AC-4": "BLOCKED"})
    report["overall_result"] = "BLOCKED"
    assert result(api, qa, report).json()["gate"] == "BLOCKED"
    assert view(api, ticket["id"])["task"]["stage"] == "QA"


def test_three_failed_repairs_pause(api, ticket):  # AT-11
    tid = ticket["id"]
    dev = to_developing(api, tid)
    fail = {"AC-1": "PASS", "AC-4": "FAIL"}
    for head in ("3" * 40, "4" * 40, "5" * 40, "6" * 40):
        result(api, dev, submission(head))
        qa = claim(api, "QA")
        result(api, qa, qa_report(head, fail))
        if view(api, tid)["task"]["execution_status"] == "PAUSED":
            break
        dev = claim(api, "DEVELOPER")
    v = view(api, tid)
    assert v["task"]["stage"] == "FIX_REQUIRED" and v["task"]["execution_status"] == "PAUSED"
    assert v["task"]["repair_count"] == 3
    assert api.post(WORKER, "/workers/claim", {"worker_id": "w1", "roles": ["DEVELOPER"]}).json()["run"] is None
    r = command(api, tid, "resume", reason="Clarified AC-4 with HR", additional_repairs=1)
    assert r.status_code == 202
    assert claim(api, "DEVELOPER")["envelope"]["repair_cycle"] is True


def test_lost_worker_is_fenced(api, ticket, db):  # AT-14
    dev = to_developing(api, ticket["id"])
    db.execute(update(Run).where(Run.status == "RUNNING")
               .values(lease_expires_at=Run.lease_expires_at - timedelta(minutes=10)))
    db.commit()
    api.post(WORKER, "/workers/hello", {"worker_id": "w2", "capabilities": {}})  # triggers the reaper
    v = view(api, ticket["id"])
    assert v["task"]["execution_status"] == "FAILED" and "stopped responding" in v["task"]["status_reason"]
    late = result(api, dev, submission(HEAD1))
    assert late.status_code == 409 and late.json()["code"] == "claim_fenced"
    assert db.scalar(select(BranchLease).where(BranchLease.released_at.is_(None))) is None
    command(api, ticket["id"], "retry", reason="worker back")
    again = claim(api, "DEVELOPER")
    assert again["envelope"]["lease_token"] > dev["envelope"]["lease_token"]


def test_cancel_during_development_keeps_cost_applies_nothing(api, ticket, db):  # AT-16
    tid = ticket["id"]
    dev = to_developing(api, tid)
    assert command(api, tid, "cancel", reason="Superseded").status_code == 202
    hb = api.post(WORKER, f"/runs/{dev['run_id']}/heartbeat", {"claim_token": dev["claim_token"]})
    assert hb.json() == {"cancel": True}
    out = result(api, dev, submission(HEAD1)).json()
    assert out["applied"] is False
    v = view(api, tid)
    assert v["task"]["stage"] == "CANCELLED" and v["task"]["head_sha"] is None
    run = db.scalar(select(Run).where(Run.id == dev["run_id"]))
    assert str(run.cost) == "0.050000"


def test_budget_race_admits_only_affordable_work(api, project, policy):  # AT-12
    pid = project["id"]
    for scope, cap in (("RUN", "40"), ("TICKET", "100"), ("PROJECT_MONTH", "50")):
        api.put("admin", f"/projects/{pid}/budgets", {"scope": scope, "cap": cap})
    a = api.post("product", f"/projects/{pid}/tasks", {"title": "A", "priority": "P1"}).json()
    b = api.post("product", f"/projects/{pid}/tasks", {"title": "B", "priority": "P1"}).json()
    command(api, a["id"], "analyze")
    command(api, b["id"], "analyze")
    states = sorted(view(api, t)["task"]["execution_status"] for t in (a["id"], b["id"]))
    assert states == ["BLOCKED", "QUEUED"]
    blocked = next(t for t in (a["id"], b["id"]) if view(api, t)["task"]["execution_status"] == "BLOCKED")
    assert "monthly budget" in view(api, blocked)["task"]["status_reason"]


def test_junior_routing_and_patch_checks(api, ticket):  # AT-10
    dev = to_developing(api, ticket["id"])
    r = api.post(WORKER, f"/runs/{dev['run_id']}/junior", {"claim_token": dev["claim_token"], "assignments": [
        {"task_type": "MOCK_DATA", "complexity": 1, "allowed_paths": ["src/fixtures/"],
         "expected_output": "users.json", "deadline": "2030-01-01T00:00:00Z"},
        {"task_type": "TYPES", "complexity": 1, "allowed_paths": ["src/auth/session.ts"],
         "expected_output": "types", "deadline": "2030-01-01T00:00:00Z"},
        {"task_type": "SIMPLE_TESTS", "complexity": 4, "allowed_paths": ["tests/"],
         "expected_output": "tests", "deadline": "2030-01-01T00:00:00Z"},
    ]}).json()["items"]
    assert [i["accepted"] for i in r] == [True, False, False]
    assert "protected area" in r[1]["reasons"][0] and "complexity" in r[2]["reasons"][0]

    child = r[0]
    patch = "diff --git a/src/fixtures/users.json b/src/fixtures/users.json\n+[]\n"
    good = {"patch": patch, "patch_ref": hashlib.sha256(patch.encode()).hexdigest(),
            "files_touched": ["src/fixtures/users.json"], "lines_changed": 1}
    ok = api.post(WORKER, f"/runs/{child['run_id']}/results", {
        "claim_token": child["claim_token"], "outcome": "SUCCEEDED", "payload": good,
        "usage": {"input_tokens": 50, "output_tokens": 20, "quality": "LOCAL"}}).json()
    assert ok["accepted"] is True

    r2 = api.post(WORKER, f"/runs/{dev['run_id']}/junior", {"claim_token": dev["claim_token"], "assignments": [
        {"task_type": "MOCK_DATA", "complexity": 1, "allowed_paths": ["src/fixtures/"],
         "expected_output": "x", "deadline": "2030-01-01T00:00:00Z"}]}).json()["items"][0]
    sneaky = {**good, "files_touched": ["src/auth/login.ts"]}
    bad = api.post(WORKER, f"/runs/{r2['run_id']}/results", {
        "claim_token": r2["claim_token"], "outcome": "SUCCEEDED", "payload": sneaky, "usage": {}}).json()
    assert bad["accepted"] is False and any("outside allowed paths" in x for x in bad["reasons"])
    # Junior work never changes the ticket; the developer run is still in charge.
    assert view(api, ticket["id"])["task"]["stage"] == "DEVELOPING"


def test_protected_path_fails_self_check(api, ticket):
    dev = to_developing(api, ticket["id"])
    out = result(api, dev, submission(HEAD1, files=("infra/deploy.sh", "src/a.ts"))).json()
    assert out["review"] == "FAILED" and "protected path" in out["findings"][0]


def test_agents_and_humans_cannot_act_as_workers(api, project, ticket):
    agent = f"dev-agent:DEVELOPER:{project['id']}:r1"
    assert api.post(agent, "/workers/claim", {"worker_id": "x", "roles": ["DEVELOPER"]}).status_code in (403, 404)
    assert api.post("eng", "/workers/claim", {"worker_id": "x", "roles": ["DEVELOPER"]}).status_code == 409


def test_team_view_and_runs_history(api, project, ticket, db):
    api.post(WORKER, "/workers/hello", {"worker_id": "w1", "capabilities": {
        "BA": {"available": True, "model": "gemini-x"},
        "JUNIOR": {"available": False, "detail": "Ollama not reachable"}}})
    team = {m["role"]: m for m in api.get("observer", f"/projects/{project['id']}/team").json()["members"]}
    assert team["BA"]["state"] == "online" and team["BA"]["member"] == "Gemini"
    assert team["DEVELOPER"]["member"] == "Claude Code" and team["DEVELOPER"]["state"] == "not_configured"
    assert team["JUNIOR"]["detail"] == "Ollama not reachable"

    command(api, ticket["id"], "analyze")
    ba = claim(api, "BA")
    api.post(WORKER, f"/runs/{ba['run_id']}/heartbeat", {
        "claim_token": ba["claim_token"], "milestone": "Drafting stories",
        "logs": [{"message": "token=ghp_" + "a" * 36}]})
    runs = api.get("observer", f"/tasks/{ticket['id']}/runs").json()["items"]
    assert runs[0]["member"] == "Gemini" and runs[0]["milestone"] == "Drafting stories"
    assert "ghp_" not in str(runs[0]["logs"])
    assert db.scalar(select(AuditEvent).where(AuditEvent.action == "run.claim")) is not None


def test_duplicate_result_submission_is_idempotent(api, ticket):
    command(api, ticket["id"], "analyze")
    ba = claim(api, "BA")
    body = {"claim_token": ba["claim_token"], "outcome": "SUCCEEDED", "payload": ba_spec(), "usage": {}}
    first = api.post(WORKER, f"/runs/{ba['run_id']}/results", body, key="res-1")
    second = api.post(WORKER, f"/runs/{ba['run_id']}/results", body, key="res-1")
    assert first.json() == second.json() and second.headers.get("idempotent-replay") == "true"
    assert len(view(api, ticket["id"])["requirements"]["versions"]) == 1

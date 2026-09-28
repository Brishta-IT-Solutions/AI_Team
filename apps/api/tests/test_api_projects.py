"""Project setup, activation, idempotency and isolation over HTTP."""

from sqlalchemy import select

from control_api.db.models import AuditEvent
from tests.conftest import agent_body, make_api


def test_only_workspace_admins_create_projects(api):
    r = api.post("product", "/projects", {"key": "NOPE", "name": "x",
                                          "classification": "SYNTHETIC"})
    assert r.status_code == 403
    assert set(r.json()) >= {"code", "message", "retryable", "correlation_id", "field_errors"}


def test_activation_denied_without_branch_enforcement(users, db):  # AT-01
    from tests.conftest import VerifiedInspector
    api = make_api(VerifiedInspector(requires_up_to_date_checks=False, required_checks=[]))
    p = api.post("admin", "/projects", {"key": "WEAK", "name": "Weak",
                                        "classification": "SYNTHETIC"}).json()
    api.put("admin", f"/projects/{p['id']}/members", {"subject": "eng",
                                                      "roles": ["ENGINEERING_LEAD"]})
    api.post("eng", f"/projects/{p['id']}/repository",
             {"installation_id": 1, "repository_id": 7, "base_branch": "main"})
    r = api.post("admin", f"/projects/{p['id']}/activate", {"expected_version": p["version"]})
    assert r.status_code == 409 and r.json()["code"] == "activation_blocked"
    missing = {m["key"]: m["detail"] for m in r.json()["details"]["missing"]}
    assert missing["requires_up_to_date_checks"].endswith("failed")
    assert "required_checks" in missing and "agent_ba" in missing and "budget_run" in missing
    assert api.get("admin", f"/projects/{p['id']}").json()["status"] == "DRAFT"
    denial = db.scalar(select(AuditEvent).where(AuditEvent.action == "project.activate"))
    assert denial is not None and denial.outcome == "DENIED"


def test_unverified_github_blocks_activation(users):
    api = make_api()  # default inspector: nothing verified
    p = api.post("admin", "/projects", {"key": "UNV", "name": "U",
                                        "classification": "SYNTHETIC"}).json()
    api.put("admin", f"/projects/{p['id']}/members", {"subject": "eng",
                                                      "roles": ["ENGINEERING_LEAD"]})
    api.post("eng", f"/projects/{p['id']}/repository",
             {"installation_id": 1, "repository_id": 7, "base_branch": "main"})
    readiness = api.get("admin", f"/projects/{p['id']}/readiness").json()
    assert readiness["ready"] is False
    assert any(i["detail"].endswith("not verified") for i in readiness["items"])


def test_full_setup_activates(project):
    assert project["status"] == "ACTIVE"


def test_idempotent_replay_and_key_reuse(api):  # FR-23
    body = {"key": "IDEM", "name": "Idem", "classification": "SYNTHETIC"}
    first = api.post("admin", "/projects", body, key="k-1")
    again = api.post("admin", "/projects", body, key="k-1")
    assert first.status_code == again.status_code == 201
    assert first.json()["id"] == again.json()["id"]
    assert again.headers.get("idempotent-replay") == "true"
    other = api.post("admin", "/projects", {**body, "name": "Changed"}, key="k-1")
    assert other.status_code == 409 and other.json()["code"] == "idempotency_reuse"
    assert len(api.get("admin", "/projects").json()["items"]) == 1


def test_missing_idempotency_key_is_rejected(api):
    r = api.client.post("/v1/projects", headers={"authorization": "Bearer dev-human:admin"},
                        json={"key": "NOKEY", "name": "x", "classification": "SYNTHETIC"})
    assert r.status_code == 400 and r.json()["code"] == "idempotency_key"


def test_cross_project_access_is_concealed(api, project):  # AT-15
    tid = api.post("product", f"/projects/{project['id']}/tasks",
                   {"title": "Secret", "priority": "P1"}).json()["id"]
    theirs = api.post("outsider", "/projects", {"key": "OTHER", "name": "Other",
                                                "classification": "SYNTHETIC"})
    assert theirs.status_code == 201
    for path in (f"/tasks/{tid}", f"/projects/{project['id']}",
                 f"/projects/{project['id']}/events", f"/projects/{project['id']}/audit"):
        r = api.get("outsider", path)
        assert r.status_code == 404, path
        assert "Secret" not in r.text
    # A foreign ticket is indistinguishable from a nonexistent one.
    foreign = api.get("outsider", f"/tasks/{tid}").json()
    missing = api.get("outsider", "/tasks/00000000-0000-0000-0000-000000000000").json()
    foreign.pop("correlation_id"), missing.pop("correlation_id")
    assert foreign == missing


def test_agent_config_versions_are_immutable(api, project):
    pid = project["id"]
    r = api.post("admin", f"/projects/{pid}/agents", {**agent_body("BA", "gemini"),
                                                      "expected_version": 1})
    assert r.status_code == 201 and r.json()["version"] == 2
    stale = api.post("admin", f"/projects/{pid}/agents", {**agent_body("BA", "gemini"),
                                                          "expected_version": 1})
    assert stale.status_code == 409


def test_secret_values_are_not_accepted_as_references(api, project):
    r = api.post("admin", f"/projects/{project['id']}/agents",
                 {**agent_body("QA", "codex"), "secret_ref": "sk-live-abc123"})
    assert r.status_code == 400

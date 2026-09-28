"""One-step local pilot setup on a project's own GitHub repository (FR-04, FR-06)."""

import pytest
from sqlalchemy import select

from control_api.db.models import AuditEvent, Repository, Run
from tests.conftest import make_api

REPO = "https://github.com/acme/tasdeeq"
PEOPLE = [{"subject": "product", "roles": ["PRODUCT_LEAD"]}, {"subject": "qa", "roles": ["QA_REVIEWER"]}]


class FakeRemote:
    def __init__(self, reachable=True, branches=("main",)):
        self.reachable, self.branches, self.calls = reachable, branches, []

    def probe(self, clone_url, branch):
        self.calls.append((clone_url, branch))
        if not self.reachable:
            return {"installation_access": False, "error": "Repository not found."}
        return {"installation_access": True, "branch_exists": branch in self.branches,
                "target_sha": "b" * 40 if branch in self.branches else None}


def setup(api, **overrides):
    body = {"key": "TASDEEQ", "name": "Tasdeeq", "repo_url": REPO, "base_branch": "main",
            "test_command": "npm test", "members": PEOPLE, **overrides}
    return api.post("admin", "/projects/setup", body)


@pytest.fixture
def remote():
    return FakeRemote()


@pytest.fixture
def api(users, remote):
    return make_api(remote_probe=remote)


def test_setup_creates_an_active_local_pilot_with_the_team(api, remote, db):
    r = setup(api)
    assert r.status_code == 201, r.json()
    p = r.json()
    assert p["status"] == "ACTIVE" and p["local_pilot"] is True and p["key"] == "TASDEEQ"
    assert remote.calls == [("https://github.com/acme/tasdeeq.git", "main")]
    assert api.get("admin", f"/projects/{p['id']}").json()["repo_url"] == "https://github.com/acme/tasdeeq.git"
    readiness = api.get("admin", f"/projects/{p['id']}/readiness").json()
    waived = [i for i in readiness["items"] if "waived" in i["detail"]]
    assert {i["key"] for i in waived} >= {"branch_protected", "required_checks", "baseline_tests"}
    team = api.get("product", f"/projects/{p['id']}/team").json()["members"]
    assert [m["member"] for m in team] == ["Gemini", "Claude Code", "Codex", "Ollama"]
    assert db.scalar(select(AuditEvent).where(AuditEvent.action == "project.setup.local_pilot"))


def test_tickets_in_a_pilot_carry_its_repository_to_the_worker(api, db):
    pid = setup(api).json()["id"]
    t = api.post("product", f"/projects/{pid}/tasks", {"title": "Verify a certificate", "priority": "P2"}).json()
    assert api.post("product", f"/tasks/{t['id']}/commands",
                    {"command": "analyze", "expected_version": t["version"]}).status_code == 202
    claim = api.post("dev-service:worker:TASDEEQ", "/workers/claim", {"worker_id": "w1", "roles": ["BA"]})
    assert claim.json()["run"]["envelope"]["project_key"] == "TASDEEQ"
    assert db.scalar(select(Run).where(Run.role == "BA")) is not None
    repo = db.scalar(select(Repository))
    assert repo.clone_url == "https://github.com/acme/tasdeeq.git" and repo.owner == "acme"


def test_unreachable_repo_or_branch_creates_nothing(users, db):
    api = make_api(remote_probe=FakeRemote(reachable=False))
    r = setup(api)
    assert r.status_code == 422 and "GITHUB_TOKEN" in r.json()["message"]
    api = make_api(remote_probe=FakeRemote(branches=("develop",)))
    r = setup(api)
    assert r.status_code == 422 and r.json()["field_errors"] == {"base_branch": "not found"}
    assert api.get("admin", "/projects").json()["items"] == []


@pytest.mark.parametrize("url", ["https://gitlab.com/acme/tasdeeq", "https://user:pw@github.com/acme/x",
                                 "file:///etc", "git@github.com:acme/tasdeeq.git"])
def test_only_plain_github_urls_are_accepted(api, remote, url):
    r = setup(api, repo_url=url)
    assert r.status_code == 422 and "repo_url" in r.json()["field_errors"]
    assert remote.calls == []


def test_only_workspace_admins_set_up_projects(api):
    assert api.post("product", "/projects/setup", {
        "key": "TASDEEQ", "name": "Tasdeeq", "repo_url": REPO, "test_command": "npm test"}).status_code == 403


def test_regular_projects_still_need_github_enforcement(api):  # AT-01 unchanged outside pilots
    p = api.post("admin", "/projects", {"key": "STRICT", "name": "Strict", "classification": "INTERNAL"}).json()
    items = api.get("admin", f"/projects/{p['id']}/readiness").json()["items"]
    assert not any("waived" in i["detail"] for i in items)

"""Each ticket's draft pull request and GitHub Copilot's advisory review of it (FR-11)."""

import uuid

from control_api.db.models import Run, Task
from control_api.services.github_reviews import copilot_review, pull_request_for
from tests.conftest import make_api
from tests.test_setup import FakeRemote, setup

CLONE = "https://github.com/acme/tasdeeq.git"
PR = {"number": 7, "url": "https://github.com/acme/tasdeeq/pull/7", "copilot_review_requested": True}


class FakeReviews:
    def __init__(self, fail=False):
        self.fail, self.calls = fail, []

    def fetch(self, owner, name, number):
        self.calls.append((owner, name, number))
        if self.fail:
            raise OSError("network down")
        copilot = {"login": "copilot-pull-request-reviewer[bot]"}
        return {
            "reviews": [{"user": copilot, "state": "COMMENTED", "body": "Two suggestions.",
                         "submitted_at": "2026-09-28T10:00:00Z", "html_url": PR["url"] + "#review-1"},
                        {"user": {"login": "someone"}, "state": "APPROVED", "body": "lgtm"}],
            "comments": [{"user": copilot, "path": "src/signin.js", "line": 3,
                          "body": "Handle a missing expiresOn. token=ghp_" + "a" * 36, "html_url": PR["url"]}],
        }


def test_only_prs_of_the_projects_own_repository_are_kept():
    assert pull_request_for(CLONE, PR)["owner"] == "acme"
    assert pull_request_for(CLONE, {**PR, "url": "https://github.com/evil/repo/pull/7"}) is None
    assert pull_request_for(CLONE, {**PR, "number": "7"}) is None
    assert pull_request_for(None, PR) is None


def test_copilot_review_is_filtered_redacted_and_survives_github_errors():
    pr = pull_request_for(CLONE, PR)
    review = copilot_review(FakeReviews(), pr)
    assert [r["state"] for r in review["reviews"]] == ["COMMENTED"]  # other reviewers aren't Copilot
    assert review["comments"][0]["path"] == "src/signin.js" and "ghp_" not in review["comments"][0]["body"]
    down = copilot_review(FakeReviews(fail=True), pr)
    assert down["available"] is False and "network down" in down["error"]


def test_ticket_endpoint_returns_the_pr_and_copilot_review(users, db):
    reviews = FakeReviews()
    api = make_api(remote_probe=FakeRemote(), review_source=reviews)
    pid = setup(api).json()["id"]
    t = api.post("product", f"/projects/{pid}/tasks", {"title": "Verify", "priority": "P2"}).json()
    assert api.get("product", f"/tasks/{t['id']}/pull-request").json() == {"pull_request": None, "copilot": None}
    task = db.get(Task, uuid.UUID(t["id"]))
    db.add(Run(project_id=task.project_id, task_id=task.id, role="DEVELOPER", attempt=1, status="SUCCEEDED",
               envelope={}, result={"pull_request": pull_request_for(CLONE, PR)}))
    db.commit()
    body = api.get("product", f"/tasks/{t['id']}/pull-request").json()
    assert body["pull_request"] == PR
    assert body["copilot"]["reviews"][0]["body"] == "Two suggestions."
    assert reviews.calls == [("acme", "tasdeeq", 7)]
    assert api.get("outsider", f"/tasks/{t['id']}/pull-request").status_code == 404

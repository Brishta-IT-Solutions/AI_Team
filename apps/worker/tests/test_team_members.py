"""OpenCode as the junior agent, and draft pull requests with GitHub Copilot review requests."""

from __future__ import annotations

import json
import os
import subprocess

import httpx

from aitc_worker import demo_repo, git, github
from aitc_worker.adapters import junior_opencode
from aitc_worker.config import Config
from aitc_worker.prompts import junior_agent_prompt

HERE = os.path.dirname(os.path.abspath(__file__))
ASSIGNMENT = {"task_type": "MOCK_DATA", "allowed_paths": ["test/fixtures/"], "expected_output": "sample identities",
              "deadline_seconds": 60}


def test_opencode_keeps_only_in_scope_edits_and_ignores_repo_config(tmp_path, monkeypatch):
    fake = os.path.join(HERE, "fakes", "opencode")
    os.chmod(fake, 0o700)
    repo = str(tmp_path / "repo")
    demo_repo.create(repo)
    with open(os.path.join(repo, "opencode.json"), "w") as f:  # a repository trying to re-enable the shell
        json.dump({"permission": {"bash": "allow"}}, f)
    git.commit_all(repo, "add opencode config")
    monkeypatch.setenv("OPENCODE_BIN", fake)
    monkeypatch.setenv("OLLAMA_URL", "http://127.0.0.1:9")
    config = Config()

    out = junior_opencode.run(config, "qwen3-coder", ASSIGNMENT, junior_agent_prompt, repo, git.head(repo),
                              str(tmp_path / "child"))
    assert out.outcome == "SUCCEEDED", out.errors
    assert out.payload["files_touched"] == ["test/fixtures/identities.json"]
    assert "escape.js" not in out.payload["patch"] and "tampered" not in out.payload["patch"]
    assert "opencode.json" not in out.payload["patch"]  # removed for the run, restored, never part of the patch
    assert out.usage == {"input_tokens": 120, "output_tokens": 25, "quality": "LOCAL"}
    assert out.provider == "opencode" and out.model == "ollama/qwen3-coder"
    assert not os.path.exists(tmp_path / "child")  # the throwaway checkout is gone


def test_opencode_availability_needs_the_cli(monkeypatch):
    monkeypatch.setenv("OPENCODE_BIN", "/nonexistent/opencode")
    assert junior_opencode.availability(Config()) == {"available": False, "provider": "opencode",
                                                      "detail": "OpenCode CLI not installed"}


def _github(handler, **env):
    config = Config()
    object.__setattr__(config, "github_token", env.get("token", "ghp_test"))
    return github.GitHub(config, transport=httpx.MockTransport(handler))


def test_draft_pull_request_is_opened_once_then_reused():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, dict(request.url.params),
                      json.loads(request.content) if request.content else None))
        assert request.headers["authorization"] == "Bearer ghp_test"
        if request.method == "GET":
            existing = [{"number": 7, "html_url": "https://github.com/acme/tasdeeq/pull/7"}] if len(calls) > 2 else []
            return httpx.Response(200, json=existing)
        if request.url.path.endswith("/pulls"):
            return httpx.Response(201, json={"number": 7, "html_url": "https://github.com/acme/tasdeeq/pull/7"})
        return httpx.Response(201, json={})

    gh = _github(handler)
    pr = gh.draft_pull_request("acme", "tasdeeq", head="feature/tas-1/abc", base="main", title="TAS-1: x", body="b")
    assert pr == {"number": 7, "url": "https://github.com/acme/tasdeeq/pull/7"}
    assert calls[0][2] == {"head": "acme:feature/tas-1/abc", "state": "open"}
    assert calls[1][3]["draft"] is True and calls[1][3]["base"] == "main"
    assert gh.draft_pull_request("acme", "tasdeeq", head="feature/tas-1/abc", base="main", title="t", body="b") == pr
    assert [c[0] for c in calls] == ["GET", "POST", "GET"]  # repair pushes reuse the same PR
    gh.request_copilot_review("acme", "tasdeeq", 7)
    assert calls[-1][1] == "/repos/acme/tasdeeq/pulls/7/requested_reviewers"
    assert calls[-1][3] == {"reviewers": ["copilot-pull-request-reviewer[bot]"]}


def test_github_errors_are_reported_not_raised_as_crashes():
    gh = _github(lambda r: httpx.Response(422, json={"message": "Copilot code review is not enabled"}))
    try:
        gh.request_copilot_review("acme", "tasdeeq", 7)
        raise AssertionError("expected GitHubError")
    except github.GitHubError as exc:
        assert "422" in str(exc) and "not enabled" in str(exc)


def test_copilot_card_says_why_it_is_off(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert "GITHUB_TOKEN" in github.copilot_availability(Config())["detail"]
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_x")
    monkeypatch.delenv("GITHUB_COPILOT_REVIEW", raising=False)
    off = github.copilot_availability(Config())
    assert not off["available"] and "Copilot Free" in off["detail"]
    monkeypatch.setenv("GITHUB_COPILOT_REVIEW", "true")
    assert github.copilot_availability(Config())["available"]
    assert github.slug("https://github.com/acme/tasdeeq.git") == ("acme", "tasdeeq")
    assert github.slug("file:///srv/local.git") is None


def test_the_worker_git_never_leaks_the_token_into_the_ai_tools(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_" + "y" * 36)
    assert "GITHUB_TOKEN" not in Config().child_env()
    assert subprocess.run(["git", "--version"], capture_output=True).returncode == 0

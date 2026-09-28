"""Per-project repositories and seats: the worker, not the AI tools, holds the GitHub token."""

from __future__ import annotations

import os
import subprocess

from aitc_worker import demo_repo, git
from aitc_worker.config import Config
from aitc_worker.main import seats


def test_each_project_gets_its_own_clone_and_pushes_only_feature_branches(tmp_path, monkeypatch):
    origin = tmp_path / "origin"
    demo_repo.create(str(origin))
    bare = tmp_path / "tasdeeq.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(origin), str(bare)], check=True)
    monkeypatch.setenv("AITC_WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_" + "x" * 36)
    config = Config()

    path, url = config.repo_for({"project_key": "TASDEEQ", "clone_url": str(bare)})
    assert path == str(tmp_path / "work" / "repos" / "TASDEEQ") and url == str(bare)
    assert config.repo_for(None) == (config.repo_path, config.repo_url)  # the default repo stays

    repo = git.ensure_repo(path, url)
    tree = git.add_worktree(repo, str(tmp_path / "wt"), branch="feature/tas-1/abc", start="origin/main")
    with open(os.path.join(tree, "NOTES.md"), "w") as f:
        f.write("hello\n")
    assert git.commit_all(tree, "TAS-1: notes")
    git.push_feature_branch(tree, "feature/tas-1/abc")
    assert "feature/tas-1/abc" in git.git(str(bare), "branch", "--list")
    # The token is never written to disk in the clone.
    assert "ghp_" not in git.git(repo, "config", "--list")
    try:
        git.push_feature_branch(tree, "main")
        raise AssertionError("pushed a protected branch")
    except git.GitError:
        pass


def test_one_scoped_identity_per_project(monkeypatch):
    monkeypatch.setenv("AITC_PROJECTS", "portal, TASDEEQ")
    monkeypatch.setenv("AITC_WORKER_ID", "desk")
    served = seats(Config())
    assert [w for w, _ in served] == ["desk:PORTAL", "desk:TASDEEQ"]
    assert served[1][1].http.headers["authorization"] == "Bearer dev-service:worker:TASDEEQ"
    monkeypatch.delenv("AITC_PROJECTS")
    assert [w for w, _ in seats(Config())] == ["desk"]

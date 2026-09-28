"""Local Git workspace. The worker, not the model, creates commits and measures the diff (FR-17)."""

from __future__ import annotations

import base64
import os
import shutil
import subprocess

from aitc_worker import demo_repo

IDENTITY = ["-c", "user.name=AI Team Control Center", "-c", "user.email=control-center@localhost"]


class GitError(RuntimeError):
    pass


def _env() -> dict[str, str]:
    """Git's own environment. The GitHub token rides in a per-process header, never in a remote URL,
    .git/config or the AI tools' environment."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        env.update({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
                    "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}"})
    return env


def git(cwd: str, *args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", *IDENTITY, *args], cwd=cwd, capture_output=True, text=True, env=_env())
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout.strip()


def ensure_repo(repo_path: str, repo_url: str) -> str:
    """Clone the project repository once, or create the demo repository if none is configured."""
    if os.path.isdir(os.path.join(repo_path, ".git")):
        if git(repo_path, "remote", check=False):
            git(repo_path, "fetch", "--prune", "origin", check=False)
        return repo_path
    os.makedirs(os.path.dirname(repo_path), exist_ok=True)
    if repo_url:
        git(os.path.dirname(repo_path), "clone", "--", repo_url, repo_path)
    else:
        demo_repo.create(repo_path)
    with open(os.path.join(repo_path, ".git", "info", "exclude"), "a") as f:
        f.write("\n.aitc/\n")  # platform files are never committed
    return repo_path


def base_ref(repo: str, base_branch: str) -> str:
    remote = f"origin/{base_branch}"
    if git(repo, "rev-parse", "--verify", "--quiet", remote, check=False):
        return remote
    if git(repo, "rev-parse", "--verify", "--quiet", base_branch, check=False):
        return base_branch
    raise GitError(f"base branch {base_branch!r} not found")


def branch_exists(repo: str, branch: str) -> bool:
    return bool(git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False))


def add_worktree(repo: str, path: str, *, branch: str | None = None, start: str | None = None,
                 detach_at: str | None = None) -> str:
    remove_worktree(repo, path)
    if detach_at:
        git(repo, "worktree", "add", "--detach", path, detach_at)
    elif branch and branch_exists(repo, branch):
        git(repo, "worktree", "add", path, branch)
    else:
        assert branch and start
        git(repo, "worktree", "add", "-b", branch, path, start)
    os.makedirs(os.path.join(path, ".aitc"), exist_ok=True)
    return path


def remove_worktree(repo: str, path: str) -> None:
    if os.path.exists(path):
        git(repo, "worktree", "remove", "--force", path, check=False)
        shutil.rmtree(path, ignore_errors=True)
    git(repo, "worktree", "prune", check=False)


def commit_all(worktree: str, message: str) -> bool:
    git(worktree, "add", "-A")
    if not git(worktree, "status", "--porcelain"):
        return False
    git(worktree, "commit", "-q", "-m", message)
    return True


def head(worktree: str) -> str:
    return git(worktree, "rev-parse", "HEAD")


def merge_base(worktree: str, ref: str) -> str:
    return git(worktree, "merge-base", "HEAD", ref)


def changed_files(worktree: str, base: str) -> list[str]:
    out = git(worktree, "diff", "--name-only", f"{base}..HEAD")
    return [line for line in out.splitlines() if line]


def staged_patch(worktree: str) -> tuple[str, list[str], int]:
    """Stage everything and return (patch, files, changed_lines) for the junior's work."""
    git(worktree, "add", "-A")
    patch = git(worktree, "diff", "--cached", "--binary") + "\n"
    files, lines = [], 0
    for row in git(worktree, "diff", "--cached", "--numstat").splitlines():
        added, removed, path = row.split("\t", 2)
        files.append(path)
        lines += (int(added) if added.isdigit() else 0) + (int(removed) if removed.isdigit() else 0)
    return patch, files, lines


def push_feature_branch(worktree: str, branch: str) -> None:
    if not branch.startswith("feature/"):  # never a protected branch
        raise GitError(f"refusing to push non-feature branch {branch}")
    git(worktree, "push", "-u", "origin", f"HEAD:refs/heads/{branch}")

"""Junior, agent edition: OpenCode drives the local Ollama model and edits files itself (FR-21).

OpenCode gets file tools only (read, search, edit); shell, web and sub-agents are denied, so the
model is never offered them. Our settings arrive through OPENCODE_CONFIG_CONTENT, which overrides
any config in the repository, and repository config files are removed from the junior's
throwaway checkout before it starts. The worker then keeps only the allowed paths and builds
the patch; the API and Claude Code review it exactly as for Ollama. Nothing is committed here.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from typing import Any

from aitc_worker import git, proc
from aitc_worker.adapters import junior_ollama
from aitc_worker.adapters.base import Outcome
from aitc_worker.config import Config

REPO_CONFIG = ("opencode.json", "opencode.jsonc", ".opencode")
_DENY = ("bash", "webfetch", "websearch", "task", "skill", "question", "external_directory", "doom_loop")
PERMISSIONS = {"edit": "allow", "read": "allow", "glob": "allow", "grep": "allow", "list": "allow",
               **{k: "deny" for k in _DENY}}


def availability(config: Config) -> dict[str, Any]:
    try:
        version = subprocess.run([config.opencode_bin, "--version"], capture_output=True, text=True,
                                 timeout=20).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return {"available": False, "provider": "opencode", "detail": "OpenCode CLI not installed"}
    model, problem = junior_ollama.pick_model(config)
    if problem:
        return {"available": False, "provider": "opencode", "version": version,
                "detail": f"needs a local model: {problem}"}
    return {"available": True, "provider": "opencode", "version": version, "model": f"ollama/{model}"}


def settings(config: Config, model: str) -> dict[str, Any]:
    return {
        "$schema": "https://opencode.ai/config.json",
        "provider": {"ollama": {"npm": "@ai-sdk/openai-compatible", "name": "Ollama (local)",
                                "options": {"baseURL": f"{config.ollama_url.rstrip('/')}/v1"},
                                "models": {model: {"name": model}}}},
        "permission": PERMISSIONS,
        "agent": {"build": {"permission": PERMISSIONS}},
        "mcp": {}, "share": "disabled", "autoupdate": False,
    }


def keep_only(worktree: str, allowed: list[str]) -> None:
    """Undo every change outside the assignment's allowed paths (the API checks again)."""
    for path in git.git(worktree, "diff", "--name-only").splitlines():
        if not junior_ollama._safe(path, allowed):
            git.git(worktree, "checkout", "--", path)
    for path in git.git(worktree, "ls-files", "--others", "--exclude-standard").splitlines():
        if not junior_ollama._safe(path, allowed):
            os.remove(os.path.join(worktree, path))


def _usage(output: str) -> dict[str, Any]:
    usage = {"input_tokens": 0, "output_tokens": 0, "quality": "LOCAL"}
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        tokens = (event.get("part") or {}).get("tokens") if event.get("type") == "step_finish" else None
        if isinstance(tokens, dict):
            usage["input_tokens"] += int(tokens.get("input") or 0)
            usage["output_tokens"] += int(tokens.get("output") or 0)
    return usage


def run(config: Config, model: str, assignment: dict[str, Any], prompt_for: Any, repo: str, start: str,
        worktree: str) -> Outcome:
    git.add_worktree(repo, worktree, detach_at=start)
    label = f"ollama/{model}"
    try:
        for name in REPO_CONFIG:  # a repository must not re-enable the shell or add tool servers
            path = os.path.join(worktree, name)
            if os.path.isdir(path):
                shutil.rmtree(path)
            elif os.path.exists(path):
                os.remove(path)
        env = {**config.child_env(), "OPENCODE_CONFIG_CONTENT": json.dumps(settings(config, model))}
        res = proc.run([config.opencode_bin, "run", "--pure", "--format", "json", "--agent", "build",
                        "--model", label, "--dir", worktree, prompt_for(assignment)],
                       cwd=worktree, env=env, timeout=assignment.get("deadline_seconds", 300))
        usage = _usage(res.output)
        if res.timed_out:
            return Outcome.failed("timeout", "OpenCode exceeded the junior time limit", usage=usage,
                                  provider="opencode", model=label)
        if res.exit_code != 0:
            return Outcome.failed("junior_error", f"OpenCode exited {res.exit_code}: {res.output[-400:]}",
                                  usage=usage, provider="opencode", model=label)
        for name in REPO_CONFIG:  # put back what we removed; it's not the junior's change
            git.git(worktree, "checkout", "--", name, check=False)
        keep_only(worktree, assignment["allowed_paths"])
        patch, touched, lines = git.staged_patch(worktree)
        if not touched:
            return Outcome.failed("empty", "the junior produced no in-scope changes", usage=usage,
                                  provider="opencode", model=label)
        return Outcome(payload={"patch": patch, "patch_ref": hashlib.sha256(patch.encode()).hexdigest(),
                                "files_touched": touched, "lines_changed": lines},
                       usage=usage, provider="opencode", model=label)
    except git.GitError as exc:
        return Outcome.failed("junior_error", f"OpenCode: {exc}", provider="opencode", model=label)
    finally:
        git.remove_worktree(repo, worktree)

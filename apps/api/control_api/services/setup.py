"""One-step setup of a local pilot project on its own GitHub repository (FR-04, FR-06).

Composes the ordinary project services, so every step is authorized and audited on its own,
and all of it commits together or not at all. The creator becomes the project's Administrator
and Engineering Lead (they approve the commands the team may run).
"""

from __future__ import annotations

import base64
import os
import re
import shlex
import subprocess
import uuid
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import select

from control_api.auth import load_human
from control_api.config import get_settings
from control_api.db.models import Project, Repository, User
from control_api.domain.permissions import HumanRole
from control_api.errors import Forbidden, Unprocessable
from control_api.services import projects
from control_api.services.audit import audit
from control_api.services.context import Ctx
from control_api.services.redaction import redact_text

GITHUB_URL = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")
TEAM_DEFAULTS = {
    "BA": ("gemini", "gemini-3.1-pro-preview"), "DEVELOPER": ("claude_code", "default"),
    "QA": ("codex", "default"), "JUNIOR": ("ollama", "qwen3-coder"),
}
BUDGET_DEFAULTS = {"PROJECT_MONTH": "500", "TICKET": "60", "RUN": "15"}
# Network settings git needs behind a proxy; nothing else from the API's environment reaches git.
_PASS_THROUGH = {"HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy",
                 "GIT_SSL_CAINFO", "SSL_CERT_FILE"}


class RemoteProbe(Protocol):
    def probe(self, clone_url: str, branch: str) -> dict[str, Any]: ...


class LsRemoteProbe:
    """Checks that a repository and branch are reachable, using the workspace GitHub token.

    The token travels in a one-off HTTP header, never in the URL, config files or logs.
    """

    def __init__(self, token: str) -> None:
        self.token = token

    def probe(self, clone_url: str, branch: str) -> dict[str, Any]:
        facts = self._ls_remote(clone_url, branch, self.token)
        if self.token and facts.get("installation_access") is False:
            # A token without access to a public repository shouldn't hide it; try anonymously.
            anonymous = self._ls_remote(clone_url, branch, "")
            if anonymous.get("installation_access"):
                return anonymous
        return facts

    def _ls_remote(self, clone_url: str, branch: str, token: str) -> dict[str, Any]:
        env = {k: v for k, v in os.environ.items() if k in _PASS_THROUGH}
        env.update({"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.path.expanduser("~"),
                    "GIT_TERMINAL_PROMPT": "0"})
        if token:
            basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
            env.update({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
                        "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}"})
        try:
            # argv list, URL already matched against GITHUB_URL, "--" ends option parsing.
            proc = subprocess.run(  # noqa: S603
                ["git", "ls-remote", "--heads", "--", clone_url, f"refs/heads/{branch}"],  # noqa: S607
                env=env, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"installation_access": False, "branch_exists": None, "error": str(exc)}
        if proc.returncode != 0:
            error = (proc.stderr.strip().splitlines() or ["git ls-remote failed"])[-1]
            if any(m in error for m in ("could not read Username", "not found", "Authentication failed")):
                error = ("it doesn't exist, or it's private and the GitHub token is missing "
                         "or has no access to it")
            return {"installation_access": False, "branch_exists": None, "error": redact_text(error)[:300]}
        sha = proc.stdout.split("\t", 1)[0].strip() if proc.stdout.strip() else None
        return {"installation_access": True, "branch_exists": bool(sha), "target_sha": sha}


class _Facts:
    def __init__(self, facts: dict[str, Any]) -> None:
        self.facts = facts

    def inspect(self, installation_id: int, repository_id: int, base_branch: str) -> dict[str, Any]:
        return self.facts


def setup_local_pilot(
    ctx: Ctx, *, key: str, name: str, description: str, repo_url: str, base_branch: str,
    test_command: str, members: list[tuple[str, list[HumanRole]]], probe: RemoteProbe,
) -> Project:
    if get_settings().env == "production":
        raise Forbidden("local pilot projects are only for development workspaces", code="permission_denied")
    match = GITHUB_URL.match(repo_url.strip())
    if not match:
        raise Unprocessable("use the repository's GitHub address",
                            field_errors={"repo_url": "must look like https://github.com/owner/name"})
    owner, repo_name = match.groups()
    clone_url = f"https://github.com/{owner}/{repo_name}.git"
    try:
        argv = shlex.split(test_command)
    except ValueError as exc:
        raise Unprocessable("test command can't be parsed", field_errors={"test_command": str(exc)}) from exc
    if not argv:
        raise Unprocessable("a test command is required", field_errors={"test_command": "required"})

    facts = probe.probe(clone_url, base_branch)
    if facts.get("installation_access") is not True:
        raise Unprocessable(
            f"can't reach {owner}/{repo_name}: {facts.get('error', 'unknown error')}. "
            "For a private repository, set GITHUB_TOKEN in .env and restart.",
            field_errors={"repo_url": "not reachable"})
    if not facts.get("branch_exists"):
        raise Unprocessable(f"branch {base_branch!r} doesn't exist in {owner}/{repo_name}",
                            field_errors={"base_branch": "not found"})

    project = projects.create_project(ctx, key=key, name=name, description=description,
                                      classification="INTERNAL")
    project.local_pilot = True
    creator = ctx.session.get(User, uuid.UUID(ctx.principal.id))
    assert creator is not None

    def as_creator() -> Ctx:  # the creator's roles in the new project apply to the next steps
        ctx.session.flush()
        return Ctx(ctx.session, load_human(ctx.session, creator), ctx.correlation_id)

    ctx = as_creator()
    projects.set_membership(ctx, project.id, subject=creator.subject,
                            roles=[HumanRole.ADMINISTRATOR, HumanRole.ENGINEERING_LEAD])
    for subject, roles in members:
        if subject != creator.subject:
            projects.set_membership(ctx, project.id, subject=subject, roles=roles)
    ctx = as_creator()

    projects.configure_repository(
        ctx, project.id, installation_id=0, repository_id=0, base_branch=base_branch,
        inspector=_Facts({**facts, "owner": owner, "name": repo_name, "mode": "local_pilot"}))
    repo = ctx.session.scalar(select(Repository).where(Repository.project_id == project.id))
    repo.clone_url = clone_url
    for role, (provider, model) in TEAM_DEFAULTS.items():
        projects.create_agent_config(ctx, project.id, {
            "role": role, "provider": provider, "model": model, "auth_method": "local_bridge",
            "adapter_version": "0.1.0", "prompt_version": "1", "prompt_hash": "0" * 64,
            "output_schema_version": "1.0"})
    for scope, cap in BUDGET_DEFAULTS.items():
        projects.set_budget(ctx, project.id, scope=scope, period="*", cap=Decimal(cap))
    projects.set_execution_policy(ctx, project.id, expected_version=project.version, protected_paths=[],
                                  commands=[{"id": "test", "argv": argv, "timeout_seconds": 900,
                                             "required": True}])
    projects.activate(ctx, project.id, expected_version=project.version)
    audit(ctx.session, ctx.principal, action="project.setup.local_pilot", object_type="project",
          object_id=project.id, project_id=project.id, correlation_id=ctx.correlation_id,
          after=projects.project_snapshot(project), policy_version=project.policy_version,
          details={"clone_url": clone_url, "base_branch": base_branch, "test_argv": argv,
                   "waived": list(projects.LOCAL_PILOT_WAIVED), "members": [s for s, _ in members]})
    return project

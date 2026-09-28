"""Worker configuration, all from environment variables (see .env.example)."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Config:
    api_url: str = field(default_factory=lambda: _env("AITC_API_URL", "http://localhost:8000"))
    token: str = field(default_factory=lambda: _env("AITC_WORKER_TOKEN", "dev-service:worker:PORTAL"))
    # Project keys this worker serves, e.g. "PORTAL,TASDEEQ". Empty: just the one AITC_WORKER_TOKEN names.
    projects: tuple[str, ...] = field(default_factory=lambda: tuple(
        k.strip().upper() for k in _env("AITC_PROJECTS").split(",") if k.strip()))
    worker_id: str = field(default_factory=lambda: _env("AITC_WORKER_ID", f"worker-{socket.gethostname()}"))
    work_dir: str = field(default_factory=lambda: _env("AITC_WORK_DIR", "/workspace"))
    repo_url: str = field(default_factory=lambda: _env("AITC_REPO_URL"))
    # Lets the worker (never the AI tools) clone private repos and push feature branches.
    github_token: str = field(default_factory=lambda: _env("GITHUB_TOKEN"))
    push_branches: bool = field(default_factory=lambda: _env("AITC_PUSH_BRANCHES").lower() == "true")
    poll_seconds: float = field(default_factory=lambda: float(_env("AITC_POLL_SECONDS", "3")))
    # Team members
    gemini_api_key: str = field(default_factory=lambda: _env("GEMINI_API_KEY"))
    gemini_model: str = field(default_factory=lambda: _env("GEMINI_MODEL", "gemini-3.1-pro-preview"))
    gemini_url: str = field(default_factory=lambda: _env(
        "GEMINI_API_URL", "https://generativelanguage.googleapis.com/v1beta/interactions"))
    claude_bin: str = field(default_factory=lambda: _env("CLAUDE_BIN", "claude"))
    claude_model: str = field(default_factory=lambda: _env("CLAUDE_MODEL"))
    codex_bin: str = field(default_factory=lambda: _env("CODEX_BIN", "codex"))
    codex_model: str = field(default_factory=lambda: _env("CODEX_MODEL"))
    ollama_url: str = field(default_factory=lambda: _env("OLLAMA_URL", "http://host.docker.internal:11434"))
    ollama_model: str = field(default_factory=lambda: _env("OLLAMA_MODEL"))
    opencode_bin: str = field(default_factory=lambda: _env("OPENCODE_BIN", "opencode"))
    # Who does junior work: "opencode" (an agent editing files with your Ollama model), "ollama"
    # (the model answers with whole files), or "auto" (OpenCode when it's installed).
    junior_engine: str = field(default_factory=lambda: _env("JUNIOR_ENGINE", "auto").lower())
    # Ask GitHub Copilot to review each ticket's pull request (needs Copilot Pro or higher).
    copilot_review: bool = field(default_factory=lambda: _env("GITHUB_COPILOT_REVIEW").lower() == "true")
    github_api_url: str = field(default_factory=lambda: _env("GITHUB_API_URL", "https://api.github.com"))

    @property
    def repo_path(self) -> str:
        return os.path.join(self.work_dir, "repo")

    def repo_for(self, repository: dict | None) -> tuple[str, str]:
        """(local path, clone URL) for a run's repository; the default repo when it names none."""
        if not repository or not repository.get("clone_url"):
            return self.repo_path, self.repo_url
        return os.path.join(self.work_dir, "repos", repository["project_key"]), repository["clone_url"]

    def child_env(self, *keep: str) -> dict[str, str]:
        """A minimal environment: each tool gets only its own credentials, never the others'."""
        base = {k: v for k, v in os.environ.items()
                if k in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "USER", "TERM", "NODE_EXTRA_CA_CERTS",
                         "SSL_CERT_FILE", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy",
                         "http_proxy", "no_proxy", "CODEX_HOME")}
        base.update({k: os.environ[k] for k in keep if os.environ.get(k)})
        base.setdefault("HOME", os.path.expanduser("~"))
        return base

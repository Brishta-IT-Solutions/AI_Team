from __future__ import annotations

import hashlib
import json
import os
import queue
import threading
from dataclasses import dataclass, field
from typing import Any

from aitc_worker import proc
from aitc_worker.client import Client
from aitc_worker.config import Config

MAX_LOG_LINE = 500


class Reporter:
    """Collects milestones and log lines; the heartbeat thread ships them to the API."""

    def __init__(self) -> None:
        self.milestone: str | None = None
        self._logs: queue.Queue[dict[str, Any]] = queue.Queue()

    def step(self, milestone: str) -> None:
        self.milestone = milestone
        self.log(milestone, "milestone")

    def log(self, message: str, type_: str = "log") -> None:
        if message.strip():
            self._logs.put({"type": type_, "message": message[:MAX_LOG_LINE]})

    def drain(self, limit: int = 50) -> list[dict[str, Any]]:
        out = []
        while len(out) < limit:
            try:
                out.append(self._logs.get_nowait())
            except queue.Empty:
                break
        return out


@dataclass
class RunContext:
    config: Config
    client: Client
    run: dict[str, Any]
    cancel: threading.Event
    reporter: Reporter

    @property
    def envelope(self) -> dict[str, Any]:
        return self.run["envelope"]

    def repo(self) -> str:
        """The run's repository, cloned on first use and fetched before every run."""
        from aitc_worker import git

        path, url = self.config.repo_for(self.envelope.get("repository"))
        return git.ensure_repo(path, url)


@dataclass
class Outcome:
    outcome: str = "SUCCEEDED"
    payload: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)
    provider: str | None = None
    model: str | None = None

    @classmethod
    def failed(cls, code: str, message: str, *, blocked: bool = False, **kw: Any) -> Outcome:
        return cls(outcome="BLOCKED" if blocked else "FAILED",
                   errors=[{"code": code, "message": message[:4000]}], **kw)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def write_spec(worktree: str, spec: dict[str, Any]) -> None:
    with open(os.path.join(worktree, ".aitc", "spec.json"), "w") as f:
        json.dump(spec, f, indent=2)


def run_checks(ctx: RunContext, worktree: str, *, label: str) -> list[dict[str, Any]]:
    """Run the Engineering-approved argv commands and keep their output as evidence."""
    results = []
    evidence_dir = os.path.join(worktree, ".aitc", "evidence")
    os.makedirs(evidence_dir, exist_ok=True)
    for command in ctx.envelope.get("commands", []):
        if ctx.cancel.is_set():
            break
        ctx.reporter.step(f"{label}: running {command['id']}")
        res = proc.run(list(command["argv"]), cwd=worktree, env=ctx.config.child_env(), cancel=ctx.cancel,
                       timeout=command.get("timeout_seconds", 600))
        path = os.path.join(evidence_dir, f"{label}-{command['id']}.log")
        with open(path, "w") as f:
            f.write(res.output)
        code = res.exit_code if not res.timed_out else 124
        ctx.reporter.log(f"{command['id']} exited {code}")
        results.append({"command_id": command["id"], "exit_code": code, "artifact_ref": sha256_file(path),
                        "required": command.get("required", True), "path": path,
                        "tail": res.output[-1500:]})
    return results


def parse_json_blob(text: str) -> Any:
    """Parse a JSON object from model output, tolerating code fences and leading chatter."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise

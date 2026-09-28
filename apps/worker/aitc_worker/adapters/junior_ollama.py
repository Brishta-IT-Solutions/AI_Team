"""Junior: a local Ollama model does narrow, mechanical work and returns a patch.

The patch is checked by the Control API and then reviewed by the developer (Claude Code).
It is never committed or pushed from here (FR-21).
"""

from __future__ import annotations

import hashlib
import os
import posixpath
from typing import Any

import httpx

from aitc_worker import git
from aitc_worker.adapters.base import Outcome, parse_json_blob
from aitc_worker.config import Config

FILES_SCHEMA = {
    "type": "object",
    "properties": {"files": {"type": "array", "items": {"type": "object", "properties": {
        "path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    "required": ["files"],
}
MAX_CONTEXT_FILES = 6
MAX_CONTEXT_BYTES = 6000


def pick_model(config: Config) -> tuple[str | None, str | None]:
    try:
        tags = httpx.get(f"{config.ollama_url}/api/tags", timeout=3).json()
    except (httpx.HTTPError, ValueError):
        return None, f"Ollama not reachable at {config.ollama_url} — is it running?"
    names = [m.get("name") for m in tags.get("models", []) if m.get("name")]
    if config.ollama_model:
        if config.ollama_model in names or f"{config.ollama_model}:latest" in names:
            return config.ollama_model, None
        return None, f"model {config.ollama_model} not pulled (have: {', '.join(names) or 'none'})"
    if not names:
        return None, "Ollama has no models; run `ollama pull qwen2.5-coder`"
    coders = [n for n in names if "coder" in n or "code" in n]
    return (coders or names)[0], None


def availability(config: Config) -> dict[str, Any]:
    model, problem = pick_model(config)
    if problem:
        return {"available": False, "provider": "ollama", "detail": problem}
    return {"available": True, "provider": "ollama", "model": model}


def _safe(path: str, allowed: list[str]) -> bool:
    norm = posixpath.normpath(path)
    if norm.startswith(("/", "..")) or "\\" in path:
        return False
    return any(norm == a.rstrip("/") or norm.startswith(a.rstrip("/") + "/") for a in allowed)


def _context(worktree: str, allowed: list[str]) -> dict[str, str]:
    files: dict[str, str] = {}
    for entry in allowed:
        full = os.path.join(worktree, entry)
        paths = [full] if os.path.isfile(full) else [
            os.path.join(d, n) for d, _, names in os.walk(full) for n in sorted(names)] if os.path.isdir(full) else []
        for p in paths[:MAX_CONTEXT_FILES]:
            with open(p, errors="replace") as f:
                files[os.path.relpath(p, worktree)] = f.read(MAX_CONTEXT_BYTES)
    return files


def run(config: Config, model: str, assignment: dict[str, Any], prompt_for: Any, repo: str, start: str,
        worktree: str) -> Outcome:
    git.add_worktree(repo, worktree, detach_at=start)
    try:
        allowed = assignment["allowed_paths"]
        prompt = prompt_for(assignment, _context(worktree, allowed))
        res = httpx.post(f"{config.ollama_url}/api/generate", timeout=assignment.get("deadline_seconds", 300),
                         json={"model": model, "prompt": prompt, "stream": False, "format": FILES_SCHEMA,
                               "options": {"temperature": 0.2}})
        res.raise_for_status()
        data = res.json()
        usage = {"input_tokens": data.get("prompt_eval_count"), "output_tokens": data.get("eval_count"),
                 "generation_ms": (data.get("eval_duration") or 0) // 1_000_000,
                 "wall_ms": (data.get("total_duration") or 0) // 1_000_000, "quality": "LOCAL"}
        files = parse_json_blob(data.get("response", "")).get("files", [])
        for item in files:
            path = str(item.get("path", ""))
            if not _safe(path, allowed):  # refused locally too; the API checks again
                continue
            full = os.path.join(worktree, path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w") as f:
                f.write(str(item.get("content", "")))
        patch, touched, lines = git.staged_patch(worktree)
        if not touched:
            return Outcome.failed("empty", "the junior produced no in-scope changes", usage=usage,
                                  provider="ollama", model=model)
        return Outcome(payload={"patch": patch, "patch_ref": hashlib.sha256(patch.encode()).hexdigest(),
                                "files_touched": touched, "lines_changed": lines},
                       usage=usage, provider="ollama", model=model)
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        return Outcome.failed("junior_error", f"Ollama: {exc}", provider="ollama", model=model)
    finally:
        git.remove_worktree(repo, worktree)

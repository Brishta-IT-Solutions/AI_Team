"""Independent QA: Codex in its own detached workspace at the exact commit under test.

Codex sees the approved spec and the code, never the developer's reasoning. The worker
runs the approved checks itself (trusted evidence), hashes every evidence file Codex cites,
and fills in the commit/spec identifiers — the model cannot choose what it is tested against.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any

from aitc_worker import git, proc
from aitc_worker.adapters.base import Outcome, RunContext, run_checks, sha256_file, write_spec
from aitc_worker.config import Config

CREDENTIALS = ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL")
RESULTS = ["PASS", "FAIL", "BLOCKED", "NOT_RUN"]
_STR = {"type": "string"}
_EVIDENCE = {"type": "array", "items": _STR}
# Strict structured-output schema: every object closed, every property required.
QA_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["criteria_results", "suites", "findings", "summary"],
    "properties": {
        "criteria_results": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["ac_id", "result", "evidence", "notes"],
            "properties": {"ac_id": _STR, "result": {"type": "string", "enum": RESULTS},
                           "evidence": _EVIDENCE, "notes": _STR}}},
        "suites": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["name", "result", "evidence"],
            "properties": {"name": _STR, "result": {"type": "string", "enum": RESULTS}, "evidence": _EVIDENCE}}},
        "findings": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["ac_id", "severity", "title", "reproduction_steps", "expected", "actual", "test_command"],
            "properties": {"ac_id": {"type": ["string", "null"]},
                           "severity": {"type": "string", "enum": ["CRITICAL", "HIGH", "MEDIUM", "LOW"]},
                           "title": _STR, "reproduction_steps": _EVIDENCE, "expected": _STR, "actual": _STR,
                           "test_command": {"type": ["string", "null"]}}}},
        "summary": _STR,
    },
}
AC_ID = re.compile(r"^[A-Z][A-Z0-9]*-[0-9]{1,4}$")


def availability(config: Config) -> dict[str, Any]:
    try:
        version = subprocess.run([config.codex_bin, "--version"], capture_output=True, text=True,
                                 timeout=20).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return {"available": False, "provider": "codex", "detail": "Codex CLI not installed"}
    codex_home = os.environ.get("CODEX_HOME", os.path.expanduser("~/.codex"))
    if not (any(os.environ.get(k) for k in CREDENTIALS[:2]) or os.path.exists(os.path.join(codex_home, "auth.json"))):
        return {"available": False, "provider": "codex", "version": version,
                "detail": "Set OPENAI_API_KEY (or CODEX_API_KEY), or sign in with `codex login`"}
    return {"available": True, "provider": "codex", "version": version, "model": config.codex_model or "default",
            "billing": "subscription" if subscription() else "api"}


def subscription() -> bool:
    """Signed in with a ChatGPT plan (`codex login`) and no API key: usage is covered by the plan."""
    return not any(os.environ.get(k) for k in CREDENTIALS[:2])


def _codex(ctx: RunContext, worktree: str, prompt: str) -> tuple[dict[str, Any] | None, dict[str, int], str]:
    cfg = ctx.config
    aitc = os.path.join(worktree, ".aitc")
    schema_path, out_path = os.path.join(aitc, "qa-schema.json"), os.path.join(aitc, "qa-report.json")
    with open(schema_path, "w") as f:
        json.dump(QA_SCHEMA, f)
    if os.path.exists(out_path):
        os.remove(out_path)
    argv = [cfg.codex_bin, "exec", "--json", "--sandbox", "workspace-write", "--skip-git-repo-check",
            "--ephemeral", "--output-schema", schema_path, "-o", out_path, "-C", worktree]
    if cfg.codex_model:
        argv += ["-m", cfg.codex_model]
    usage = {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0}

    def on_line(line: str) -> None:
        try:
            event = json.loads(line)
        except ValueError:
            return
        u = event.get("usage") if isinstance(event, dict) else None
        if isinstance(u, dict) and event.get("type") == "turn.completed":
            for k in usage:
                usage[k] += int(u.get(k) or 0)
        item = event.get("item") if isinstance(event, dict) else None
        if isinstance(item, dict) and event.get("type") == "item.completed":
            kind = item.get("type", "")
            detail = item.get("command") or item.get("text") or ""
            ctx.reporter.log(f"{kind}: {str(detail)[:300]}")

    res = proc.run(argv + ["-"], cwd=worktree, env=cfg.child_env(*CREDENTIALS, "CODEX_HOME"),
                   timeout=ctx.envelope.get("deadline_seconds", 1800), cancel=ctx.cancel, on_line=on_line,
                   stdin=prompt)
    if res.cancelled:
        raise InterruptedError("cancelled")
    if res.timed_out:
        raise TimeoutError("Codex exceeded the QA time limit")
    if res.exit_code != 0 or not os.path.exists(out_path):
        return None, usage, res.output[-800:]
    try:
        with open(out_path) as f:
            return json.load(f), usage, ""
    except ValueError:
        return None, usage, "Codex wrote an invalid report"


def _evidence(worktree: str, paths: list[str]) -> list[str]:
    refs = []
    for p in paths:
        full = os.path.realpath(os.path.join(worktree, p))
        if full.startswith(os.path.realpath(worktree) + os.sep) and os.path.isfile(full):
            refs.append(sha256_file(full))
    return refs


def build_report(env: dict[str, Any], raw: dict[str, Any], checks: list[dict[str, Any]],
                 worktree: str) -> dict[str, Any]:
    approved = set(env["approved_ac_ids"])
    criteria = []
    for c in raw.get("criteria_results", []):
        if c.get("ac_id") not in approved or any(x["ac_id"] == c["ac_id"] for x in criteria):
            continue
        refs = _evidence(worktree, c.get("evidence", []))
        result = c.get("result") if c.get("result") in RESULTS else "NOT_RUN"
        if result == "PASS" and not refs:
            result = "NOT_RUN"  # never PASS without evidence the platform can hash
        criteria.append({"ac_id": c["ac_id"], "result": result, "evidence_refs": refs})
    suites = [{"name": f"approved:{c['command_id']}", "mandatory": bool(c["required"]),
               "result": "PASS" if c["exit_code"] == 0 else "FAIL", "evidence_refs": [c["artifact_ref"]]}
              for c in checks]
    suites += [{"name": f"codex:{s.get('name', 'suite')}"[:80], "mandatory": False,
                "result": s.get("result") if s.get("result") in RESULTS else "NOT_RUN",
                "evidence_refs": _evidence(worktree, s.get("evidence", []))} for s in raw.get("suites", [])]
    findings = []
    for f in raw.get("findings", []):
        ac = f.get("ac_id") if isinstance(f.get("ac_id"), str) and AC_ID.match(f["ac_id"]) else None
        findings.append({"ac_id": ac, "severity": f.get("severity", "MEDIUM"),
                         "title": str(f.get("title") or "Unnamed defect")[:300],
                         "reproduction_steps": [str(s)[:1000] for s in f.get("reproduction_steps") or []] or
                         ["See QA evidence"],
                         "expected": str(f.get("expected", ""))[:2000], "actual": str(f.get("actual", ""))[:2000],
                         "test_command": f.get("test_command")})
    results = [c["result"] for c in criteria] + [s["result"] for s in suites if s["mandatory"]]
    overall = next((v for v in ("FAIL", "BLOCKED", "NOT_RUN") if v in results), "PASS" if results else "NOT_RUN")
    return {"spec_version": env["spec_version"], "head_sha": env["head_sha"], "base_sha": env["base_sha"],
            "criteria_results": criteria, "suites": suites, "findings": findings, "overall_result": overall}


def run(ctx: RunContext) -> Outcome:
    env, cfg = ctx.envelope, ctx.config
    repo = ctx.repo()
    worktree = os.path.join(cfg.work_dir, "qa", ctx.run["run_id"])
    usage: dict[str, Any] = {"quality": "UNKNOWN"}
    model = cfg.codex_model or "default"
    try:
        ctx.reporter.step(f"Checking out {env['head_sha'][:10]} for independent QA")
        git.add_worktree(repo, worktree, detach_at=env["head_sha"])
        write_spec(worktree, env["spec"])
        checks = run_checks(ctx, worktree, label="qa")
        ctx.reporter.step("Codex is verifying every acceptance criterion")
        raw, tokens, error = _codex(ctx, worktree, env["prompt"])
        usage.update(tokens)
        reported = {c.get("ac_id") for c in (raw or {}).get("criteria_results", [])}
        missing = sorted(set(env["approved_ac_ids"]) - reported)
        if raw is None or missing:  # one repair attempt for an invalid structured result (FR-24)
            ctx.reporter.step("Asking Codex to complete its report")
            extra = f"\n\nYour report must cover exactly these criteria: {', '.join(env['approved_ac_ids'])}."
            raw, tokens, error = _codex(ctx, worktree, env["prompt"] + extra)
            for k, v in tokens.items():
                usage[k] = usage.get(k, 0) + v
        if raw is None:
            return Outcome.failed("invalid_result", f"Codex produced no usable report. {error}", usage=usage,
                                  provider="codex", model=model)
        report = build_report(env, raw, checks, worktree)
        if subscription():
            usage["quality"] = "SUBSCRIPTION"
        ctx.reporter.log(str(raw.get("summary", ""))[:500], "summary")
        return Outcome(payload=report, usage=usage, provider="codex", model=model)
    except InterruptedError:
        return Outcome.failed("cancelled", "run cancelled", usage=usage, provider="codex", model=model)
    except (TimeoutError, git.GitError) as exc:
        return Outcome.failed("qa_error", str(exc), usage=usage, provider="codex", model=model)
    finally:
        git.remove_worktree(repo, worktree)

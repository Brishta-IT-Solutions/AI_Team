"""Developer and UX: Claude Code in headless mode, in an isolated git worktree.

Hardening for unattended runs (FR-27): project settings, hooks and MCP servers from the
repository are not loaded; anything that would need a permission prompt is denied.
The worker commits and measures the diff itself; Claude's claims are not evidence.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from aitc_worker import git, github, proc
from aitc_worker.adapters import junior_ollama, junior_opencode
from aitc_worker.adapters.base import Outcome, RunContext, parse_json_blob, run_checks, write_spec
from aitc_worker.config import Config

TOOLS = "Read Edit Write Glob Grep Bash TodoWrite"
CREDENTIALS = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_BASE_URL")


def subscription() -> bool:
    """A Claude Pro/Max token (from `claude setup-token`) and no API key: usage is covered by the plan."""
    return bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")) and not os.environ.get("ANTHROPIC_API_KEY")


def availability(config: Config) -> dict[str, Any]:
    try:
        version = subprocess.run([config.claude_bin, "--version"], capture_output=True, text=True,
                                 timeout=20).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return {"available": False, "provider": "claude_code", "detail": "Claude Code CLI not installed"}
    if not any(os.environ.get(k) for k in CREDENTIALS[:2]):
        return {"available": False, "provider": "claude_code", "version": version,
                "detail": "Set ANTHROPIC_API_KEY, or CLAUDE_CODE_OAUTH_TOKEN from `claude setup-token`"}
    return {"available": True, "provider": "claude_code", "version": version,
            "model": config.claude_model or "default", "billing": "subscription" if subscription() else "api"}


def _claude(ctx: RunContext, worktree: str, prompt: str, *, resume: str | None, timeout: float) -> dict[str, Any]:
    cfg = ctx.config
    argv = [cfg.claude_bin, "-p", prompt, "--output-format", "json", "--permission-mode", "acceptEdits",
            "--permission-prompts", "none", "--allowedTools", TOOLS, "--setting-sources", "user",
            "--strict-mcp-config"]
    if cfg.claude_model:
        argv += ["--model", cfg.claude_model]
    if resume:
        argv += ["--resume", resume]
    res = proc.run(argv, cwd=worktree, env=cfg.child_env(*CREDENTIALS), timeout=timeout, cancel=ctx.cancel)
    if res.cancelled:
        raise InterruptedError("cancelled")
    if res.timed_out:
        raise TimeoutError("Claude Code exceeded the development time limit")
    try:
        data = parse_json_blob(res.output.strip().splitlines()[-1] if res.output.strip() else "")
    except (ValueError, IndexError):
        data = {}
    if res.exit_code != 0 or data.get("is_error") or not data:
        raise RuntimeError(f"Claude Code failed (exit {res.exit_code}): {(data.get('result') or res.output)[-800:]}")
    return data


def _cost(first: float, second: float | None) -> float:
    """Resumed sessions may report cumulative cost; charge only the delta (FR-25)."""
    if second is None:
        return first
    return second if second >= first else first + second


def run(ctx: RunContext) -> Outcome:
    env, cfg = ctx.envelope, ctx.config
    repo = ctx.repo()
    worktree = os.path.join(cfg.work_dir, "dev", ctx.run["run_id"])
    usage: dict[str, Any] = {"quality": "PROVIDER_ESTIMATE"}
    model = cfg.claude_model or "default"
    try:
        ctx.reporter.step("Preparing the feature branch")
        base = git.base_ref(repo, env["base_branch"])
        git.add_worktree(repo, worktree, branch=env["branch"], start=base)
        write_spec(worktree, env["spec"])
        deadline = env.get("deadline_seconds", 2700)

        ctx.reporter.step("Claude Code is implementing the ticket")
        first = _claude(ctx, worktree, env["prompt"], resume=None, timeout=deadline)
        cost_a = float(first.get("total_cost_usd") or 0)
        cost_b: float | None = None
        ctx.reporter.log((first.get("result") or "")[:500], "summary")

        junior_summary = _delegate(ctx, repo, worktree)
        accepted = [j for j in junior_summary if j.get("accepted")]
        if accepted:
            ctx.reporter.step("Claude Code is reviewing the junior's patches")
            from aitc_worker.prompts import junior_review_prompt
            second = _claude(ctx, worktree, junior_review_prompt(accepted), resume=first.get("session_id"),
                             timeout=deadline / 2)
            cost_b = float(second.get("total_cost_usd") or 0)
            ctx.reporter.log((second.get("result") or "")[:500], "summary")

        checks = run_checks(ctx, worktree, label="self-check")
        ctx.reporter.step("Committing the candidate")
        git.commit_all(worktree, f"{env['task_key']}: {env['title']}"[:72])
        head = git.head(worktree)
        base_sha = git.merge_base(worktree, base)
        limitations: list[str] = []
        pull_request = None
        wants_push = cfg.push_branches or (env.get("repository") or {}).get("push_feature_branches")
        if wants_push and git.git(repo, "remote", check=False):
            ctx.reporter.step(f"Pushing {env['branch']} to GitHub")
            try:  # the candidate stands without the push; you just won't see it on GitHub yet
                git.push_feature_branch(worktree, env["branch"])
                pull_request = _pull_request(ctx, limitations)
            except git.GitError as exc:
                ctx.reporter.log(f"push failed: {exc}")
                limitations.append(f"feature branch not pushed to GitHub: {str(exc)[-300:]}")
        spend = round(_cost(cost_a, cost_b), 6)
        if subscription():
            usage.update({"quality": "SUBSCRIPTION", "notional_cost_usd": spend})
        else:
            usage["cost_usd"] = spend
        usage.update({
                      "input_tokens": (first.get("usage") or {}).get("input_tokens"),
                      "output_tokens": (first.get("usage") or {}).get("output_tokens")})
        summary = (first.get("result") or "Implemented the ticket.")[:8000]
        return Outcome(payload={
            "submission": {"head_sha": head, "base_sha": base_sha,
                           "files_changed": git.changed_files(worktree, base_sha), "summary": summary,
                           "tests": [{k: c[k] for k in ("command_id", "exit_code", "artifact_ref")} for c in checks],
                           "known_limitations": limitations},
            "junior": junior_summary,
            "checks": [{"command_id": c["command_id"], "exit_code": c["exit_code"], "tail": c["tail"]} for c in checks],
            "pull_request": pull_request,
        }, usage=usage, provider="claude_code", model=model)
    except InterruptedError:
        return Outcome.failed("cancelled", "run cancelled", usage=usage, provider="claude_code", model=model)
    except (RuntimeError, TimeoutError, git.GitError) as exc:
        return Outcome.failed("developer_error", str(exc), usage=usage, provider="claude_code", model=model)
    finally:
        git.remove_worktree(repo, worktree)


def _pull_request(ctx: RunContext, limitations: list[str]) -> dict[str, Any] | None:
    """A draft PR for the pushed branch, and a Copilot review request when that's switched on."""
    env, cfg = ctx.envelope, ctx.config
    target = github.slug((env.get("repository") or {}).get("clone_url") or "")
    if not (target and cfg.github_token):
        return None
    owner, name = target
    body = (f"Opened by the AI Software Team Control Center for **{env['task_key']}**.\n\n"
            "Built by Claude Code and tested independently by Codex in the Control Center. "
            "Merge only after the ticket's merge approval there.")
    try:
        pr = github.GitHub(cfg).draft_pull_request(
            owner, name, head=env["branch"], base=env["base_branch"],
            title=f"{env['task_key']}: {env['title']}"[:250], body=body)
    except (github.GitHubError, OSError) as exc:
        ctx.reporter.log(f"pull request not opened: {exc}")
        limitations.append(f"draft pull request not opened: {str(exc)[-300:]}")
        return None
    pr["copilot_review_requested"] = False
    if cfg.copilot_review:
        try:
            github.GitHub(cfg).request_copilot_review(owner, name, pr["number"])
            pr["copilot_review_requested"] = True
            ctx.reporter.log(f"asked GitHub Copilot to review {pr['url']}")
        except (github.GitHubError, OSError) as exc:
            limitations.append(f"GitHub Copilot review not requested: {str(exc)[-300:]}")
    return pr


def _junior_engine(cfg: Any) -> tuple[Any, Any, str]:
    """(adapter, prompt, name) for junior work: OpenCode when available and not switched off."""
    from aitc_worker.prompts import junior_agent_prompt, junior_prompt

    if cfg.junior_engine != "ollama" and junior_opencode.availability(cfg)["available"]:
        return junior_opencode, junior_agent_prompt, "OpenCode"
    return junior_ollama, junior_prompt, "Ollama"


def _delegate(ctx: RunContext, repo: str, worktree: str) -> list[dict[str, Any]]:
    """Send Claude's delegations to the junior (OpenCode or Ollama) and stage accepted patches for review."""
    path = os.path.join(worktree, ".aitc", "delegations.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            requested = json.load(f)
        assert isinstance(requested, list)
    except (ValueError, AssertionError):
        ctx.reporter.log("ignored malformed .aitc/delegations.json")
        return []
    model, problem = junior_ollama.pick_model(ctx.config)
    if problem:
        ctx.reporter.log(f"junior unavailable: {problem}; Claude keeps this work")
        return [{"accepted": False, "reasons": [problem]}]
    from datetime import UTC, datetime, timedelta

    engine, prompt_for, member = _junior_engine(ctx.config)
    deadline = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
    routed = ctx.client.junior(ctx.run["run_id"], ctx.run["claim_token"],
                               [{**r, "deadline": deadline} for r in requested[:10] if isinstance(r, dict)])
    start = git.head(worktree)
    summary: list[dict[str, Any]] = []
    os.makedirs(os.path.join(worktree, ".aitc", "patches"), exist_ok=True)
    for i, item in enumerate(routed, start=1):
        if not item.get("accepted"):
            summary.append({"accepted": False, "reasons": item.get("reasons", [])})
            continue
        a = item["assignment"]
        ctx.reporter.step(f"{member} is doing junior task {i}: {a['task_type'].lower()}")
        child_tree = os.path.join(ctx.config.work_dir, "junior", item["run_id"])
        out = engine.run(ctx.config, model, a, prompt_for, repo, start, child_tree)
        verdict = ctx.client.result(item["run_id"], item["claim_token"], outcome=out.outcome, payload=out.payload,
                                    usage=out.usage, errors=out.errors, provider=out.provider, model=out.model)
        entry = {"accepted": bool(verdict.get("accepted")), "reasons": verdict.get("reasons", []),
                 "task_type": a["task_type"], "expected_output": a["expected_output"]}
        if entry["accepted"]:
            entry["file"] = f"junior-{i}.patch"
            with open(os.path.join(worktree, ".aitc", "patches", entry["file"]), "w") as f:
                f.write(out.payload["patch"])
        summary.append(entry)
    return summary

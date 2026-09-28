"""Prompts for the worker-internal junior delegation loop (the API renders the main prompts)."""

from __future__ import annotations

from typing import Any


def junior_prompt(assignment: dict[str, Any], files: dict[str, str]) -> str:
    current = "\n\n".join(f"=== {path} ===\n{content}" for path, content in files.items()) or "(no existing files)"
    return f"""You are a careful junior developer. Do exactly this and nothing more:

TASK TYPE: {assignment["task_type"]}
EXPECTED OUTPUT: {assignment["expected_output"]}
YOU MAY ONLY WRITE THESE PATHS: {", ".join(assignment["allowed_paths"])}

CURRENT CONTENT OF RELEVANT FILES:
{current}

Reply with JSON: {{"files": [{{"path": "relative/path", "content": "full new file content"}}]}}
Only include files you create or change. Keep content complete, not partial.
"""


def junior_review_prompt(patches: list[dict[str, Any]]) -> str:
    listing = "\n".join(
        f"- {p['file']}: {p['task_type']} — {p['expected_output']}" for p in patches
    )
    return f"""The junior model returned these patches for the work you delegated. Each is in
.aitc/patches/. Review every one as you would a pull request from a junior colleague:
{listing}

For each patch: if it is correct and in scope, apply it with `git apply .aitc/patches/<file>`;
otherwise do not apply it and fix or finish that work yourself. Then run the approved checks
again and make them pass. End with one line per patch: "<file>: applied" or "<file>: rejected — reason".
"""

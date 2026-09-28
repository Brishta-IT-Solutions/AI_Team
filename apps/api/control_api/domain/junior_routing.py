"""Supervised Ollama routing (FRD FR-21).

The broker decides eligibility independently of any score the model proposes.
Protected areas always route to the developer role (Claude), whatever the score.
"""

from __future__ import annotations

import fnmatch
import posixpath
from dataclasses import dataclass

from control_api.contracts import JuniorAssignment, JuniorResult

MAX_ELIGIBLE_COMPLEXITY = 2
DEFAULT_MAX_FILES = 10
DEFAULT_MAX_CHANGED_LINES = 500

# Keyword fragments that mark a path as protected. Matched per path segment.
PROTECTED_FRAGMENTS = (
    "auth", "login", "session", "permission", "rbac", "acl", "role",
    "payment", "billing", "checkout", "migration", "migrations", "alembic",
    "workflow", "policy", "policies", "secret", "credential", ".github",
)


@dataclass(frozen=True)
class RoutingDecision:
    accepted: bool
    reasons: tuple[str, ...]


def _normalize(path: str) -> str | None:
    if "\\" in path or "\x00" in path:
        return None
    norm = posixpath.normpath(path)
    if norm.startswith("/") or norm == ".." or norm.startswith("../"):
        return None
    return norm


def is_protected(path: str, extra_protected_globs: tuple[str, ...] = ()) -> bool:
    norm = (_normalize(path) or path).lower()
    segments = norm.replace(".", "/").replace("_", "/").replace("-", "/").split("/")
    if any(frag in segments for frag in PROTECTED_FRAGMENTS):
        return True
    if any(fragment in norm for fragment in ("auth", "payment", "migration", "permission")):
        return True
    return any(fnmatch.fnmatch(norm, g) for g in extra_protected_globs)


def route_assignment(
    a: JuniorAssignment, extra_protected_globs: tuple[str, ...] = ()
) -> RoutingDecision:
    reasons: list[str] = []
    if a.complexity > MAX_ELIGIBLE_COMPLEXITY:
        reasons.append(f"complexity {a.complexity} exceeds junior limit {MAX_ELIGIBLE_COMPLEXITY}")
    for path in a.allowed_paths:
        if _normalize(path) is None:
            reasons.append(f"invalid path {path!r}")
        elif is_protected(path, extra_protected_globs):
            reasons.append(f"protected area {path!r} routes to the developer role")
    return RoutingDecision(not reasons, tuple(reasons))


def _within(path: str, allowed: list[str]) -> bool:
    norm = _normalize(path)
    if norm is None:
        return False
    for pattern in allowed:
        p = _normalize(pattern.rstrip("/")) or ""
        if fnmatch.fnmatch(norm, p) or norm == p or norm.startswith(p + "/"):
            return True
    return False


def validate_patch(
    a: JuniorAssignment,
    r: JuniorResult,
    *,
    max_files: int = DEFAULT_MAX_FILES,
    max_lines: int = DEFAULT_MAX_CHANGED_LINES,
    extra_protected_globs: tuple[str, ...] = (),
) -> RoutingDecision:
    """Reject out-of-scope patches before the developer reviews them (AT-10)."""
    reasons: list[str] = []
    if len(r.files_touched) > max_files:
        reasons.append(f"{len(r.files_touched)} files exceeds limit {max_files}")
    if r.lines_changed > max_lines:
        reasons.append(f"{r.lines_changed} changed lines exceeds limit {max_lines}")
    for path in r.files_touched:
        if not _within(path, a.allowed_paths):
            reasons.append(f"{path!r} is outside allowed paths")
        elif is_protected(path, extra_protected_globs):
            reasons.append(f"{path!r} is a protected area")
    return RoutingDecision(not reasons, tuple(reasons))

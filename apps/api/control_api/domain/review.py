"""Pure review rules for the delivery loop (FR-17, FR-18).

No I/O. The API feeds these with persisted records and validated worker reports.
"""

from __future__ import annotations

import fnmatch
import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum

from control_api.contracts import CriterionResult, DeveloperSubmission, QAReport, Severity

# Paths no developer submission may touch, whatever the project policy says.
ALWAYS_PROTECTED = (".aitc/*", ".aitc/**", ".git/*", ".github/workflows/*")
BLOCKING_SEVERITIES = frozenset({Severity.CRITICAL, Severity.HIGH})


class QAGate(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"  # a functional failure: defects, then the repair loop
    BLOCKED = "BLOCKED"  # could not be verified: never a pass, never a functional failure


@dataclass(frozen=True)
class QAOutcome:
    gate: QAGate
    reasons: tuple[str, ...]
    failed_criteria: tuple[str, ...]


def evaluate_qa(report: QAReport, mandatory_ac_ids: set[str]) -> QAOutcome:
    """Mandatory criteria and suites must all PASS and no Critical/High finding may be open.

    FAIL beats BLOCKED: if anything failed functionally, the developer has work to do.
    Optional criteria that fail become defects but do not block readiness.
    """
    results = {c.ac_id: c.result for c in report.criteria_results}
    failed = sorted(a for a in mandatory_ac_ids if results.get(a) is CriterionResult.FAIL)
    unverified = sorted(
        a for a in mandatory_ac_ids
        if results.get(a) in (CriterionResult.BLOCKED, CriterionResult.NOT_RUN, None)
    )
    failed_suites = [s.name for s in report.suites if s.mandatory and s.result is CriterionResult.FAIL]
    unverified_suites = [
        s.name for s in report.suites
        if s.mandatory and s.result in (CriterionResult.BLOCKED, CriterionResult.NOT_RUN)
    ]
    severe = [f.title for f in report.findings if f.severity in BLOCKING_SEVERITIES]

    reasons: list[str] = []
    if failed:
        reasons.append(f"mandatory criteria failed: {', '.join(failed)}")
    if failed_suites:
        reasons.append(f"mandatory suites failed: {', '.join(failed_suites)}")
    if severe:
        reasons.append(f"{len(severe)} critical/high finding(s)")
    if reasons:
        return QAOutcome(QAGate.FAIL, tuple(reasons), tuple(failed))
    if unverified or unverified_suites:
        what = unverified + unverified_suites
        return QAOutcome(QAGate.BLOCKED, (f"not verified: {', '.join(what)}",), ())
    return QAOutcome(QAGate.PASS, (), ())


def defect_signature(ac_id: str | None, title: str) -> str:
    """Stable key so a repeated failure updates one defect instead of creating another."""
    normalized = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
    normalized = re.sub(r"\b[0-9a-f]{7,40}\b|\b\d+\b", "#", normalized)  # SHAs, line numbers
    return hashlib.sha256(f"{ac_id or '-'}|{normalized}".encode()).hexdigest()


def dev_review(
    submission: DeveloperSubmission,
    *,
    required_commands: list[str],
    protected_globs: list[str],
) -> list[str]:
    """DEV_REVIEW: automated scope validation plus the developer's self-check (FR-17).

    Findings are actionable sentences; an empty list means the candidate may go to QA.
    The evidence is the worker's own git diff and captured exit codes, not model claims.
    """
    findings: list[str] = []
    if not submission.files_changed:
        findings.append("no files changed")
    for path in submission.files_changed:
        for glob in (*ALWAYS_PROTECTED, *protected_globs):
            if fnmatch.fnmatch(path, glob):
                findings.append(f"{path} is a protected path ({glob})")
                break
    ran = {t.command_id: t.exit_code for t in submission.tests}
    for command in required_commands:
        if command not in ran:
            findings.append(f"required check '{command}' did not run")
        elif ran[command] != 0:
            findings.append(f"required check '{command}' failed with exit code {ran[command]}")
    return findings

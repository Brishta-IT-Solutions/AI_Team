"""Approval scope binding (FRD FR-15).

An approval is valid only for the exact tuple it was granted against. The scope
hash is a SHA-256 over canonical JSON of that tuple, so any change to spec, head,
base, QA report or policy produces a different hash and a stale approval can no
longer satisfy the gate.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any


class Gate(StrEnum):
    REQUIREMENTS = "REQUIREMENTS"
    MERGE = "MERGE"
    RELEASE = "RELEASE"


class ApprovalDecision(StrEnum):
    APPROVED = "APPROVED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    REJECTED = "REJECTED"
    REVOKED = "REVOKED"  # system-recorded supersession; never an edit


def canonical_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def requirements_scope(spec_version: int, content_hash: str) -> str:
    return canonical_hash(
        {"gate": Gate.REQUIREMENTS, "spec_version": spec_version, "content_hash": content_hash}
    )


def merge_scope(
    *, spec_hash: str, head_sha: str, base_sha: str, qa_report_id: str, policy_version: int
) -> str:
    return canonical_hash(
        {
            "gate": Gate.MERGE,
            "spec_hash": spec_hash,
            "head_sha": head_sha,
            "base_sha": base_sha,
            "qa_report_id": qa_report_id,
            "policy_version": policy_version,
        }
    )


def release_scope(
    *, merged_commit: str, build_digest: str, environment: str, staging_evidence_hash: str
) -> str:
    return canonical_hash(
        {
            "gate": Gate.RELEASE,
            "merged_commit": merged_commit,
            "build_digest": build_digest,
            "environment": environment,
            "staging_evidence_hash": staging_evidence_hash,
        }
    )

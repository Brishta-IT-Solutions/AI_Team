"""Secret redaction before persistence (FR-27, NFR-07).

Defense in depth only: secrets must never reach agents or logs in the first place.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"

_PATTERNS = [
    re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"),  # GitHub tokens
    re.compile(r"github_pat_[A-Za-z0-9_]{22,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),  # Anthropic
    re.compile(r"sk-(?:proj-)?[A-Za-z0-9_\-]{20,}"),  # OpenAI-style
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),  # Google API keys
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key id
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),  # Slack
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),  # JWT
    re.compile(
        r"(?i)((?:password|passwd|secret|token|api[_-]?key)\s*[=:]\s*)(['\"]?)[^\s'\"]{6,}\2"
    ),
]

_SENSITIVE_KEYS = re.compile(r"(?i)^(password|passwd|secret|token|api[_-]?key|authorization)$")


def redact_text(value: str) -> str:
    for pattern in _PATTERNS:
        if pattern.groups >= 1:
            value = pattern.sub(lambda m: f"{m.group(1)}{REDACTED}", value)
        else:
            value = pattern.sub(REDACTED, value)
    return value


def redact(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {
            k: (REDACTED if isinstance(k, str) and _SENSITIVE_KEYS.match(k) else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    return value

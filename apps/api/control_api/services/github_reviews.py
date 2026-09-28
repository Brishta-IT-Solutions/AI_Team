"""GitHub Copilot's review of a ticket's pull request, read back for the Code tab (FR-11).

Advisory only: Copilot's findings are shown to the humans deciding the merge; they never move a
ticket or count as an approval. The worker opens the pull request; the API only reads it, and only
for the repository linked to the ticket's project.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any, Protocol

from control_api.services.redaction import redact_text

COPILOT_LOGINS = {"copilot-pull-request-reviewer[bot]", "copilot-pull-request-reviewer", "Copilot"}
_REPO = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")
MAX_TEXT = 4000


def pull_request_for(clone_url: str | None, pr: Any) -> dict[str, Any] | None:
    """A worker-reported PR, accepted only if it's a PR of this project's own repository."""
    m = _REPO.match(clone_url or "")
    if not (m and isinstance(pr, dict) and isinstance(pr.get("number"), int)):
        return None
    owner, name = m.groups()
    url = f"https://github.com/{owner}/{name}/pull/{pr['number']}"
    if pr.get("url") != url:
        return None
    return {"number": pr["number"], "url": url, "owner": owner, "name": name,
            "copilot_review_requested": bool(pr.get("copilot_review_requested"))}


class ReviewSource(Protocol):
    def fetch(self, owner: str, name: str, number: int) -> dict[str, list[dict[str, Any]]]: ...


class GitHubRest:
    def __init__(self, token: str, api_url: str = "https://api.github.com") -> None:
        if not api_url.startswith("https://"):
            raise ValueError("the GitHub API is only reached over https")
        self.token, self.api_url = token, api_url.rstrip("/")

    def _get(self, path: str) -> list[dict[str, Any]]:
        req = urllib.request.Request(f"{self.api_url}{path}?per_page=100", headers={  # noqa: S310 - https only
            "authorization": f"Bearer {self.token}", "accept": "application/vnd.github+json",
            "x-github-api-version": "2022-11-28", "user-agent": "aitc-control-api"})
        with urllib.request.urlopen(req, timeout=15) as res:  # noqa: S310 - https only
            return json.loads(res.read())

    def fetch(self, owner: str, name: str, number: int) -> dict[str, list[dict[str, Any]]]:
        base = f"/repos/{owner}/{name}/pulls/{number}"
        return {"reviews": self._get(f"{base}/reviews"), "comments": self._get(f"{base}/comments")}


def copilot_review(source: ReviewSource, pr: dict[str, Any]) -> dict[str, Any]:
    try:
        raw = source.fetch(pr["owner"], pr["name"], pr["number"])
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"available": False, "error": f"couldn't read the pull request from GitHub: {exc}"}

    def mine(item: dict[str, Any]) -> bool:
        return (item.get("user") or {}).get("login") in COPILOT_LOGINS

    reviews = [{"state": r.get("state"), "body": redact_text(r.get("body") or "")[:MAX_TEXT],
                "submitted_at": r.get("submitted_at"), "url": r.get("html_url")}
               for r in raw.get("reviews", []) if mine(r)]
    comments = [{"path": c.get("path"), "line": c.get("line") or c.get("original_line"),
                 "body": redact_text(c.get("body") or "")[:MAX_TEXT], "url": c.get("html_url")}
                for c in raw.get("comments", []) if mine(c)][:100]
    return {"available": True, "reviews": reviews, "comments": comments}

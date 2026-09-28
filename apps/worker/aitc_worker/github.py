"""Draft pull requests on GitHub and Copilot review requests, with the workspace token.

The worker opens a draft PR for each ticket's feature branch so you can review and merge it on
GitHub. It never merges, approves or pushes a protected branch (FR-16, FR-17).
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from aitc_worker.config import Config

COPILOT_REVIEWER = "copilot-pull-request-reviewer[bot]"
_SLUG = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")


class GitHubError(RuntimeError):
    pass


def slug(clone_url: str) -> tuple[str, str] | None:
    m = _SLUG.match(clone_url or "")
    return (m.group(1), m.group(2)) if m else None


def copilot_availability(config: Config) -> dict[str, Any]:
    base = {"provider": "github_copilot"}
    if not config.github_token:
        return {**base, "available": False, "detail": "Needs GITHUB_TOKEN to open pull requests for it to review"}
    if not config.copilot_review:
        return {**base, "available": False,
                "detail": "Off. Copilot code review needs Copilot Pro or higher (Copilot Free doesn't include it); "
                          "then set GITHUB_COPILOT_REVIEW=true"}
    return {**base, "available": True, "model": "Copilot code review"}


class GitHub:
    def __init__(self, config: Config, transport: httpx.BaseTransport | None = None) -> None:
        self.http = httpx.Client(base_url=config.github_api_url.rstrip("/"), timeout=30, transport=transport,
                                 headers={"authorization": f"Bearer {config.github_token}",
                                          "accept": "application/vnd.github+json",
                                          "x-github-api-version": "2022-11-28"})

    def _call(self, method: str, path: str, **kw: Any) -> Any:
        res = self.http.request(method, path, **kw)
        if res.status_code >= 400:
            try:
                message = res.json().get("message", "")
            except ValueError:
                message = res.text[:200]
            raise GitHubError(f"GitHub {res.status_code}: {message}")
        return res.json() if res.content else {}

    def draft_pull_request(self, owner: str, name: str, *, head: str, base: str, title: str,
                           body: str) -> dict[str, Any]:
        """The open PR for this branch, or a new draft one."""
        existing = self._call("GET", f"/repos/{owner}/{name}/pulls",
                              params={"head": f"{owner}:{head}", "state": "open"})
        pr = existing[0] if existing else self._call(
            "POST", f"/repos/{owner}/{name}/pulls",
            json={"title": title, "head": head, "base": base, "body": body, "draft": True})
        return {"number": int(pr["number"]), "url": str(pr["html_url"])}

    def request_copilot_review(self, owner: str, name: str, number: int) -> None:
        self._call("POST", f"/repos/{owner}/{name}/pulls/{number}/requested_reviewers",
                   json={"reviewers": [COPILOT_REVIEWER]})

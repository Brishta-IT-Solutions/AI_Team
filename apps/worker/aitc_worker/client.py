"""Control API client with retries. Mutations carry deterministic idempotency keys."""

from __future__ import annotations

import time
import uuid
from typing import Any

import httpx

from aitc_worker.config import Config


class ApiError(RuntimeError):
    def __init__(self, status: int, body: dict[str, Any]) -> None:
        super().__init__(f"{status} {body.get('code')}: {body.get('message')}")
        self.status, self.body = status, body


class Client:
    def __init__(self, config: Config, transport: httpx.BaseTransport | None = None,
                 token: str | None = None) -> None:
        self.http = httpx.Client(base_url=config.api_url.rstrip("/") + "/v1", timeout=30,
                                 headers={"authorization": f"Bearer {token or config.token}"},
                                 transport=transport)

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None,
              key: str | None = None, attempts: int = 3) -> dict[str, Any]:
        headers = {"idempotency-key": key or uuid.uuid4().hex}
        delay = 2.0
        for attempt in range(1, attempts + 1):
            try:
                res = self.http.request(method, path, json=body, headers=headers)
            except httpx.TransportError:
                if attempt == attempts:
                    raise
                time.sleep(delay)
                delay *= 2
                continue
            if res.status_code in (429, 502, 503, 504) and attempt < attempts:
                time.sleep(float(res.headers.get("retry-after", delay)))
                delay *= 2
                continue
            data = res.json() if res.content else {}
            if res.status_code >= 400:
                raise ApiError(res.status_code, data)
            return data
        raise RuntimeError("unreachable")

    def hello(self, worker_id: str, capabilities: dict[str, Any], version: str) -> None:
        self._call("POST", "/workers/hello", {"worker_id": worker_id, "capabilities": capabilities,
                                              "version": version})

    def claim(self, worker_id: str, roles: list[str]) -> dict[str, Any] | None:
        return self._call("POST", "/workers/claim", {"worker_id": worker_id, "roles": roles}).get("run")

    def heartbeat(self, run_id: str, claim_token: str, milestone: str | None,
                  logs: list[dict[str, Any]]) -> dict[str, Any]:
        return self._call("POST", f"/runs/{run_id}/heartbeat",
                          {"claim_token": claim_token, "milestone": milestone, "logs": logs}, attempts=1)

    def junior(self, run_id: str, claim_token: str, assignments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self._call("POST", f"/runs/{run_id}/junior",
                          {"claim_token": claim_token, "assignments": assignments})["items"]

    def result(self, run_id: str, claim_token: str, *, outcome: str, payload: dict[str, Any],
               usage: dict[str, Any], errors: list[dict[str, Any]] | None = None,
               provider: str | None = None, model: str | None = None) -> dict[str, Any]:
        return self._call("POST", f"/runs/{run_id}/results", {
            "claim_token": claim_token, "outcome": outcome, "payload": payload, "usage": usage,
            "errors": errors or [], "provider": provider, "model": model,
        }, key=f"result-{run_id}-{claim_token[:12]}", attempts=5)

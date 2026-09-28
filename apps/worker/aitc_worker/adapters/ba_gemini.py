"""Business analyst: Gemini via the Interactions API with structured JSON output.

Antigravity has no supported unattended interface, so it is used through the manual
brief/import path in the web app; this adapter covers the automated Gemini path.
"""

from __future__ import annotations

from typing import Any

import httpx

from aitc_worker.adapters.base import Outcome, RunContext, parse_json_blob
from aitc_worker.config import Config

_ID = {"type": "string"}
BA_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "goal": {"type": "string"},
        "stories": {"type": "array", "items": {"type": "object", "properties": {
            "id": _ID, "as_a": {"type": "string"}, "i_want": {"type": "string"}, "so_that": {"type": "string"}},
            "required": ["id", "as_a", "i_want", "so_that"]}},
        "business_rules": {"type": "array", "items": {"type": "object", "properties": {
            "id": _ID, "statement": {"type": "string"}}, "required": ["id", "statement"]}},
        "acceptance_criteria": {"type": "array", "items": {"type": "object", "properties": {
            "id": _ID, "statement": {"type": "string"}, "verification": {"type": "string"},
            "mandatory": {"type": "boolean"}}, "required": ["id", "statement", "verification", "mandatory"]}},
        "ux_flows": {"type": "array", "items": {"type": "object", "properties": {
            "id": _ID, "name": {"type": "string"}, "steps": {"type": "array", "items": {"type": "string"}}},
            "required": ["id", "name", "steps"]}},
        "edge_cases": {"type": "array", "items": {"type": "string"}},
        "dependencies": {"type": "array", "items": {"type": "string"}},
        "questions": {"type": "array", "items": {"type": "object", "properties": {
            "id": _ID, "text": {"type": "string"}, "blocking": {"type": "boolean"},
            "resolution": {"type": ["string", "null"]}}, "required": ["id", "text", "blocking", "resolution"]}},
    },
    "required": ["goal", "stories", "business_rules", "acceptance_criteria", "ux_flows", "edge_cases",
                 "dependencies", "questions"],
}
REQUIRED = BA_SCHEMA["required"]


def availability(config: Config) -> dict[str, Any]:
    if not config.gemini_api_key:
        return {"available": False, "provider": "gemini",
                "detail": "Set GEMINI_API_KEY, or use 'Copy brief for Antigravity' on the ticket"}
    return {"available": True, "provider": "gemini", "model": config.gemini_model}


def _output_text(data: dict[str, Any]) -> str:
    texts = [c.get("text", "") for step in data.get("steps", []) if step.get("type") == "model_output"
             for c in step.get("content", []) if c.get("type") == "text"]
    if texts:
        return "".join(texts)
    for cand in data.get("candidates", []):  # generateContent-shaped responses
        return "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
    return str(data.get("output_text", ""))


def run(ctx: RunContext, transport: httpx.BaseTransport | None = None) -> Outcome:
    cfg = ctx.config
    prompt = ctx.envelope["prompt"]
    usage = {"input_tokens": 0, "output_tokens": 0, "quality": "UNKNOWN"}
    with httpx.Client(timeout=ctx.envelope.get("deadline_seconds", 600), transport=transport) as http:
        for attempt in (1, 2):  # one repair attempt for an invalid structured result (FR-24)
            ctx.reporter.step("Gemini is drafting the specification" if attempt == 1
                              else "Asking Gemini to fix its JSON")
            res = http.post(cfg.gemini_url, headers={"x-goog-api-key": cfg.gemini_api_key},
                            json={"model": cfg.gemini_model, "input": prompt,
                                  "response_format": {"type": "text", "mime_type": "application/json",
                                                      "schema": BA_SCHEMA}})
            if res.status_code in (401, 403):
                return Outcome.failed("auth", "Gemini rejected the API key", blocked=True,
                                      provider="gemini", model=cfg.gemini_model)
            if res.status_code >= 400:
                return Outcome.failed("provider_error", f"Gemini returned {res.status_code}: {res.text[:300]}",
                                      provider="gemini", model=cfg.gemini_model, usage=usage)
            data = res.json()
            u = data.get("usage") or {}
            usage["input_tokens"] += int(u.get("total_input_tokens") or 0)
            usage["output_tokens"] += int(u.get("total_output_tokens") or 0)
            try:
                spec = parse_json_blob(_output_text(data))
                missing = [k for k in REQUIRED if k not in spec]
                if missing:
                    raise ValueError(f"missing fields: {', '.join(missing)}")
                return Outcome(payload=spec, usage=usage, provider="gemini", model=cfg.gemini_model)
            except (ValueError, TypeError) as exc:
                prompt = (f"{ctx.envelope['prompt']}\n\nYour previous reply was invalid ({exc}). "
                          "Reply again with ONLY the complete JSON object.")
    return Outcome.failed("invalid_result", "Gemini did not return a valid specification after one repair",
                          usage=usage, provider="gemini", model=cfg.gemini_model)

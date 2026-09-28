"""End to end: real Control API + real worker + real git, with stand-ins for the four AI tools.

Gemini and Ollama are served by a local fake HTTP server; `claude` and `codex` are small
scripts that behave like the CLIs' headless modes. Everything else is production code.
Needs PostgreSQL (AITC_DATABASE_URL); skipped when it is unreachable.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
API_DIR = os.path.join(HERE, "..", "..", "api")
os.environ.setdefault("AITC_DATABASE_URL", "postgresql+psycopg://aitc:aitc@localhost:5432/aitc_test")
os.environ["AITC_ENV"] = "test"

httpx = pytest.importorskip("httpx")
control_api = pytest.importorskip("control_api")

SPEC = {
    "goal": "Expatriates sign in with a national identity and get renewal guidance when it has expired.",
    "stories": [{"id": "ST-1", "as_a": "expatriate employee", "i_want": "to sign in", "so_that": "I reach HR"}],
    "business_rules": [{"id": "BR-1", "statement": "Expired identities are refused."}],
    "acceptance_criteria": [
        {"id": "AC-1", "statement": "A valid identity signs in", "verification": "unit", "mandatory": True},
        {"id": "AC-4", "statement": "An expired identity is refused with renewal guidance",
         "verification": "unit", "mandatory": True}],
    "ux_flows": [], "edge_cases": [], "dependencies": [],
    "questions": [{"id": "Q-1", "text": "Which tenant?", "blocking": True, "resolution": "EU-2"}],
}
CALLS: dict[str, int] = {"gemini": 0, "ollama": 0}


class FakeProviders(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quiet
        pass

    def _send(self, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        assert self.path == "/api/tags"
        self._send({"models": [{"name": "qwen2.5-coder:7b"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        if self.path == "/v1beta/interactions":
            assert self.headers["x-goog-api-key"] == "fake-gemini"
            assert body["response_format"]["mime_type"] == "application/json"
            CALLS["gemini"] += 1
            self._send({"steps": [{"type": "model_output", "content": [{"type": "text", "text": json.dumps(SPEC)}]}],
                        "usage": {"total_input_tokens": 900, "total_output_tokens": 400}})
        elif self.path == "/api/generate":
            CALLS["ollama"] += 1
            assert body["stream"] is False and "format" in body
            files = {"files": [{"path": "test/fixtures/identities.json", "content": '[{"expiresOn": "1999-01-01"}]\n'},
                               {"path": "src/auth/escape.js", "content": "// out of scope\n"}]}
            self._send({"response": json.dumps(files), "prompt_eval_count": 120, "eval_count": 60,
                        "eval_duration": 5_000_000, "total_duration": 9_000_000})
        else:
            self.send_error(404)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    from alembic import command
    from alembic.config import Config as AlembicConfig
    from control_api.db.models import Base, User
    from control_api.db.session import get_engine, get_sessionmaker
    from sqlalchemy import text

    try:
        with get_engine().begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL unavailable: {exc}")
    cfg = AlembicConfig(os.path.join(API_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(API_DIR, "migrations"))
    command.upgrade(cfg, "head")
    assert Base.metadata.tables
    with get_sessionmaker()() as s:
        for subject, admin in (("admin", True), ("product", False), ("eng", False)):
            s.add(User(subject=subject, display_name=subject, workspace_admin=admin))
        s.commit()

    import uvicorn
    from control_api.main import create_app

    class Verified:
        def inspect(self, installation_id, repository_id, base_branch):
            return {"installation_access": True, "branch_exists": True, "branch_protected": True,
                    "requires_up_to_date_checks": True, "required_checks": ["test"], "baseline_tests_passed": True}

    api_port, fake_port = _free_port(), _free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(inspector=Verified()), port=api_port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    providers = ThreadingHTTPServer(("127.0.0.1", fake_port), FakeProviders)
    threading.Thread(target=providers.serve_forever, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)

    for fake in ("claude", "codex"):  # checkouts don't always keep the executable bit
        os.chmod(os.path.join(HERE, "fakes", fake), 0o700)
    work = tmp_path_factory.mktemp("work")
    os.environ.update({
        "AITC_API_URL": f"http://127.0.0.1:{api_port}", "AITC_WORKER_TOKEN": "dev-service:worker:DEMO",
        "AITC_WORK_DIR": str(work), "AITC_WORKER_ID": "e2e-worker",
        "GEMINI_API_KEY": "fake-gemini", "GEMINI_API_URL": f"http://127.0.0.1:{fake_port}/v1beta/interactions",
        "GEMINI_MODEL": "gemini-test", "ANTHROPIC_API_KEY": "fake-anthropic", "OPENAI_API_KEY": "fake-openai",
        "CLAUDE_BIN": os.path.join(HERE, "fakes", "claude"), "CODEX_BIN": os.path.join(HERE, "fakes", "codex"),
        "OLLAMA_URL": f"http://127.0.0.1:{fake_port}", "OLLAMA_MODEL": "",
        "JUNIOR_ENGINE": "ollama",  # this loop exercises the Ollama junior; OpenCode has its own test
    })
    yield f"http://127.0.0.1:{api_port}/v1"
    server.should_exit = True
    providers.shutdown()


def _h(who: str) -> dict[str, str]:
    return {"authorization": f"Bearer dev-human:{who}", "idempotency-key": uuid.uuid4().hex}


def test_the_whole_team_delivers_a_ticket(stack):
    from aitc_worker import git
    from aitc_worker.client import Client
    from aitc_worker.config import Config
    from aitc_worker.main import VERSION, capabilities, step

    api = httpx.Client(base_url=stack, timeout=30)
    p = api.post("/projects", headers=_h("admin"),
                 json={"key": "DEMO", "name": "Demo", "classification": "SYNTHETIC"}).json()
    pid = p["id"]
    for subject, roles in (("product", ["PRODUCT_LEAD"]), ("eng", ["ENGINEERING_LEAD"])):
        api.put(f"/projects/{pid}/members", headers=_h("admin"), json={"subject": subject, "roles": roles})
    api.post(f"/projects/{pid}/repository", headers=_h("eng"),
             json={"installation_id": 1, "repository_id": 1, "base_branch": "main"})
    for role, provider in (("BA", "gemini"), ("DEVELOPER", "claude_code"), ("QA", "codex"), ("JUNIOR", "ollama")):
        api.post(f"/projects/{pid}/agents", headers=_h("admin"), json={
            "role": role, "provider": provider, "model": "pinned", "auth_method": "api_key_ref",
            "adapter_version": "0.1.0", "prompt_version": "1", "prompt_hash": "0" * 64})
    for scope, cap in (("PROJECT_MONTH", "100"), ("TICKET", "20"), ("RUN", "2")):
        api.put(f"/projects/{pid}/budgets", headers=_h("admin"), json={"scope": scope, "cap": cap})
    from control_api.db.session import get_engine
    from sqlalchemy import text
    with get_engine().begin() as conn:
        conn.execute(text("UPDATE agent_configs SET connection_test = '{\"passed\": true}'"))
    version = api.get(f"/projects/{pid}", headers=_h("admin")).json()["version"]
    r = api.put(f"/projects/{pid}/execution-policy", headers=_h("eng"), json={
        "commands": [{"id": "test", "argv": ["npm", "test"]}], "protected_paths": [], "expected_version": version})
    assert r.status_code == 200, r.text
    assert api.post(f"/projects/{pid}/activate", headers=_h("admin"),
                    json={"expected_version": r.json()["version"]}).status_code == 200

    config = Config()
    client = Client(config)
    git.ensure_repo(config.repo_path, config.repo_url)
    caps = capabilities(config)
    assert all(caps[r]["available"] for r in ("BA", "DEVELOPER", "QA", "JUNIOR")), caps
    client.hello(config.worker_id, caps, VERSION)
    roles = ["BA", "DEVELOPER", "QA"]

    task = api.post(f"/projects/{pid}/tasks", headers=_h("product"),
                    json={"title": "Expatriate sign-in", "priority": "P1"}).json()
    tid = task["id"]

    def view() -> dict:
        return api.get(f"/tasks/{tid}", headers=_h("product")).json()

    api.post(f"/tasks/{tid}/commands", headers=_h("product"),
             json={"command": "analyze", "expected_version": view()["task"]["version"]})
    assert step(config, client, roles)  # Gemini writes the spec
    v = view()
    assert v["task"]["stage"] == "REQUIREMENTS_APPROVAL" and CALLS["gemini"] == 1
    r = api.post(f"/tasks/{tid}/approvals", headers=_h("product"), json={
        "gate": "REQUIREMENTS", "decision": "APPROVED", "expected_version": v["task"]["version"],
        "scope_hash": v["requirements"]["approval_scope_hash"]})
    assert r.status_code == 200, r.text

    for _ in range(8):  # Claude → Codex (fails AC-4) → Claude repair → Codex retest
        if view()["task"]["stage"] == "MERGE_APPROVAL":
            break
        assert step(config, client, roles), view()["task"]
    v = view()
    assert v["task"]["stage"] == "MERGE_APPROVAL", v["task"]
    assert v["task"]["repair_count"] == 1

    runs = api.get(f"/tasks/{tid}/runs", headers=_h("product")).json()["items"]
    members = [r["member"] for r in reversed(runs) if r["parent_run_id"] is None]
    assert members == ["Gemini", "Claude Code", "Codex", "Claude Code", "Codex"]
    junior = [r for r in runs if r["role"] == "JUNIOR"]
    # Ollama also tried to write src/auth/escape.js; the worker dropped it before building the
    # patch, so the API accepted a clean, in-scope patch for Claude to review.
    assert len(junior) == 1 and junior[0]["result"]["accepted"] is True
    assert junior[0]["result"]["files_touched"] == ["test/fixtures/identities.json"]
    assert junior[0]["cost_quality"] == "LOCAL" and CALLS["ollama"] == 1
    first_dev = next(r for r in reversed(runs) if r["role"] == "DEVELOPER")
    routed = first_dev["result"]["junior"]
    assert any("protected area" in " ".join(j.get("reasons", [])) for j in routed)  # src/auth/ refused
    assert first_dev["cost"] == "0.300000"  # cumulative resumed cost counted once

    qa = api.get(f"/tasks/{tid}/qa", headers=_h("product")).json()
    assert [r["verdict"] for r in qa["reports"]] == ["PASS", "FAIL"]
    assert qa["defects"][0]["status"] == "RESOLVED" and qa["defects"][0]["ac_id"] == "AC-4"

    repo = config.repo_path
    branch = git.git(repo, "branch", "--list", "feature/*").strip().lstrip("* ").strip()
    assert branch.startswith("feature/demo-1/")
    head = git.git(repo, "rev-parse", branch)
    assert head == v["task"]["head_sha"]
    assert "renew" in git.git(repo, "show", f"{branch}:src/signin.js")
    assert git.git(repo, "rev-parse", "main") != head  # the protected branch is untouched
    tree = git.git(repo, "ls-tree", "-r", "--name-only", branch)
    assert ".aitc" not in tree and "src/auth/escape.js" not in tree
    assert "test/fixtures/identities.json" in tree  # Claude reviewed and applied the junior's patch

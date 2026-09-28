from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from typing import Any

os.environ.setdefault(
    "AITC_DATABASE_URL", "postgresql+psycopg://aitc:aitc@localhost:5432/aitc_test"
)
os.environ["AITC_ENV"] = "test"

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from control_api.db.models import Base, User  # noqa: E402
from control_api.db.session import get_engine, get_sessionmaker  # noqa: E402
from control_api.main import create_app  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class VerifiedInspector:
    """Stands in for the GitHub broker with a fully compliant repository."""

    def __init__(self, **overrides: Any) -> None:
        self.overrides = overrides

    def inspect(self, installation_id: int, repository_id: int, base_branch: str) -> dict:
        return {
            "installation_access": True, "branch_exists": True, "branch_protected": True,
            "requires_up_to_date_checks": True, "required_checks": ["ci/test"],
            "baseline_tests_passed": True, "owner": "acme", "name": "portal",
            "target_sha": "a" * 40, **self.overrides,
        }


@pytest.fixture(scope="session", autouse=True)
def _schema() -> Iterator[None]:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    cfg = Config(os.path.join(HERE, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(HERE, "migrations"))
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with get_engine().begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
def db():
    with get_sessionmaker()() as session:
        yield session


USERS = {
    "admin": dict(display_name="Ada Admin", workspace_admin=True),
    "product": dict(display_name="Pat Product"),
    "eng": dict(display_name="Eli Engineer"),
    "qa": dict(display_name="Quinn QA"),
    "observer": dict(display_name="Olive Observer"),
    "outsider": dict(display_name="Oscar Outsider", workspace_admin=True),
}


@pytest.fixture
def users(db) -> dict[str, User]:
    created = {}
    for subject, fields in USERS.items():
        u = User(subject=subject, **fields)
        db.add(u)
        created[subject] = u
    db.commit()
    return created


class Api:
    def __init__(self, client: TestClient) -> None:
        self.client = client

    def _headers(self, who: str, key: str | None) -> dict[str, str]:
        token = who if who.startswith("dev-") else f"dev-human:{who}"
        return {"authorization": f"Bearer {token}", "idempotency-key": key or uuid.uuid4().hex}

    def get(self, who: str, path: str, **params: Any):
        return self.client.get(f"/v1{path}", headers=self._headers(who, None), params=params)

    def post(self, who: str, path: str, body: dict, key: str | None = None):
        return self.client.post(f"/v1{path}", headers=self._headers(who, key), json=body)

    def put(self, who: str, path: str, body: dict, key: str | None = None):
        return self.client.put(f"/v1{path}", headers=self._headers(who, key), json=body)


def make_api(inspector=None, remote_probe=None, review_source=None) -> Api:
    return Api(TestClient(create_app(inspector=inspector, remote_probe=remote_probe, review_source=review_source),
                          raise_server_exceptions=False))


@pytest.fixture
def api(users) -> Api:
    return make_api(VerifiedInspector())


@pytest.fixture
def project(api: Api) -> dict:
    """An ACTIVE project with product/eng/qa/observer members and full readiness."""
    p = api.post("admin", "/projects", {"key": "PORTAL", "name": "Portal",
                                        "classification": "SYNTHETIC"}).json()
    pid = p["id"]
    for subject, roles in {"product": ["PRODUCT_LEAD"], "eng": ["ENGINEERING_LEAD"],
                           "qa": ["QA_REVIEWER"], "observer": ["OBSERVER"]}.items():
        assert api.put("admin", f"/projects/{pid}/members",
                       {"subject": subject, "roles": roles}).status_code == 200
    assert api.post("eng", f"/projects/{pid}/repository", {
        "installation_id": 1, "repository_id": 42, "base_branch": "main"}).status_code == 200
    for role, provider in {"BA": "gemini", "DEVELOPER": "claude_code", "QA": "codex",
                           "JUNIOR": "ollama"}.items():
        assert api.post("admin", f"/projects/{pid}/agents", agent_body(role, provider)).status_code == 201
    for scope, cap in (("PROJECT_MONTH", "500"), ("TICKET", "60"), ("RUN", "5")):
        assert api.put("admin", f"/projects/{pid}/budgets",
                       {"scope": scope, "cap": cap}).status_code == 200
    mark_connection_tests_passed(pid)
    r = api.post("admin", f"/projects/{pid}/activate", {"expected_version": p["version"]})
    assert r.status_code == 200, r.json()
    return r.json()


def agent_body(role: str, provider: str) -> dict:
    return {"role": role, "provider": provider, "model": f"{provider}-pinned",
            "auth_method": "api_key_ref", "adapter_version": "0.1.0", "prompt_version": "1",
            "prompt_hash": "0" * 64, "secret_ref": f"vault://aitc/{provider}"}


def mark_connection_tests_passed(project_id: str) -> None:
    # Adapter connection tests arrive with the adapters; record a pass directly for now.
    with get_engine().begin() as conn:
        conn.execute(text("UPDATE agent_configs SET connection_test = '{\"passed\": true}' "
                          "WHERE project_id = :p"), {"p": project_id})


def ba_spec(*, blocking_open: bool = False, extra_ac: bool = False) -> dict:
    spec = {
        "goal": "Let expatriate employees sign in with their national identity provider.",
        "stories": [{"id": "ST-1", "as_a": "expatriate employee", "i_want": "to sign in",
                     "so_that": "I can reach HR services"}],
        "business_rules": [{"id": "BR-1", "statement": "Expired identities are refused."}],
        "acceptance_criteria": [
            {"id": "AC-1", "statement": "Valid identity signs in", "verification": "e2e",
             "mandatory": True},
            {"id": "AC-4", "statement": "Expired identity is refused with guidance",
             "verification": "e2e", "mandatory": True},
        ],
        "questions": [{"id": "Q-1", "text": "Which identity provider tenant?", "blocking": True,
                       "resolution": None if blocking_open else "Tenant EU-2"}],
    }
    if extra_ac:
        spec["acceptance_criteria"].append({"id": "AC-5", "statement": "Audit sign-in",
                                            "verification": "unit", "mandatory": False})
    return spec

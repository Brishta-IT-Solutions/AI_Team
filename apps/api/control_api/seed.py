"""Development seed: users, one active demo project, tickets at several stages.

    python -m control_api.seed

Refuses to run when AITC_ENV=production. Repository facts are seeded, not read from
GitHub, and are labelled as such.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select, update

from control_api.auth import load_human
from control_api.config import get_settings
from control_api.contracts import BASpecification
from control_api.db.models import AgentConfig, Project, User
from control_api.db.session import get_sessionmaker
from control_api.domain.approvals import ApprovalDecision, Gate
from control_api.domain.permissions import AgentRole, HumanRole
from control_api.services import projects, tasks
from control_api.services.context import Ctx

USERS = [
    ("admin", "Ada Admin", True), ("product", "Pat Product", False), ("eng", "Eli Engineer", False),
    ("qa", "Quinn QA", False), ("observer", "Olive Observer", False),
]


class SeedInspector:
    def inspect(self, installation_id: int, repository_id: int, base_branch: str) -> dict:
        return {"installation_access": True, "branch_exists": True, "branch_protected": True,
                "requires_up_to_date_checks": True, "required_checks": ["ci/test"],
                "baseline_tests_passed": True, "owner": "demo", "name": "portal",
                "target_sha": "0" * 40, "source": "seed — not verified against GitHub"}


def spec(blocking_open: bool) -> BASpecification:
    return BASpecification.model_validate({
        "goal": "Expatriate employees sign in with their national identity provider.",
        "stories": [{"id": "ST-1", "as_a": "expatriate employee", "i_want": "to sign in with my national ID",
                     "so_that": "I can reach HR services without a separate password"}],
        "business_rules": [{"id": "BR-1", "statement": "Expired identities are refused."},
                           {"id": "BR-2", "statement": "Residency status must be active."}],
        "acceptance_criteria": [
            {"id": "AC-1", "statement": "A valid identity signs in and lands on the HR home page.",
             "verification": "e2e", "mandatory": True},
            {"id": "AC-4", "statement": "An expired identity is refused with renewal guidance.",
             "verification": "e2e", "mandatory": True},
            {"id": "AC-6", "statement": "Sign-in events are audit logged.", "verification": "integration",
             "mandatory": False},
        ],
        "questions": [{"id": "Q-1", "text": "Which identity-provider tenant serves expatriates?",
                       "blocking": True, "resolution": None if blocking_open else "Tenant EU-2"}],
    })


def main() -> None:
    if get_settings().env == "production":
        raise SystemExit("refusing to seed a production environment")
    factory = get_sessionmaker()
    with factory() as s:
        if s.scalar(select(Project).where(Project.key == "PORTAL")):
            print("already seeded")
            return
        for subject, name, admin in USERS:
            if not s.scalar(select(User).where(User.subject == subject)):
                s.add(User(subject=subject, display_name=name, workspace_admin=admin))
        s.commit()

        def ctx(subject: str) -> Ctx:
            user = s.scalar(select(User).where(User.subject == subject))
            return Ctx(s, load_human(s, user), correlation_id=f"seed-{uuid.uuid4().hex[:8]}")

        p = projects.create_project(ctx("admin"), key="PORTAL", name="Employee Portal",
                                    description="Pilot repository", classification="SYNTHETIC")
        s.commit()
        for subject, role in [("product", HumanRole.PRODUCT_LEAD), ("eng", HumanRole.ENGINEERING_LEAD),
                              ("qa", HumanRole.QA_REVIEWER), ("observer", HumanRole.OBSERVER)]:
            projects.set_membership(ctx("admin"), p.id, subject=subject, roles=[role])
        s.commit()
        projects.configure_repository(ctx("eng"), p.id, installation_id=1, repository_id=1,
                                      base_branch="main", inspector=SeedInspector())
        providers = {
            AgentRole.BA: ("gemini", "gemini-pinned"), AgentRole.DEVELOPER: ("claude_code", "claude-pinned"),
            AgentRole.QA: ("codex", "codex-pinned"), AgentRole.JUNIOR: ("ollama", "local-pinned"),
        }
        for role, (provider, model) in providers.items():
            projects.create_agent_config(ctx("admin"), p.id, {
                "role": role.value, "provider": provider, "model": model, "auth_method": "api_key_ref",
                "adapter_version": "0.1.0", "prompt_version": "1", "prompt_hash": "0" * 64,
                "secret_ref": f"vault://aitc/{provider}", "output_schema_version": "1.0"})
        s.execute(update(AgentConfig).where(AgentConfig.project_id == p.id)
                  .values(connection_test={"passed": True, "source": "seed"}))
        for scope, cap in [("PROJECT_MONTH", "500"), ("TICKET", "60"), ("RUN", "15")]:
            projects.set_budget(ctx("admin"), p.id, scope=scope, period="*", cap=Decimal(cap))
        s.commit()
        projects.activate(ctx("admin"), p.id, expected_version=p.version)
        s.commit()

        pl = ctx("product")
        tasks.create_task(pl, p.id, title="Export audit log as signed CSV", description="", priority="P2",
                          dependencies=[])
        blocked = tasks.create_task(pl, p.id, title="Expatriate sign-in with national ID", priority="P1",
                                    description="Replace password sign-in for expatriate staff.",
                                    dependencies=[])
        ready = tasks.create_task(pl, p.id, title="Residency status banner on HR home", priority="P2",
                                  description="Show residency expiry and renewal link.", dependencies=[])
        s.commit()
        for t, open_q in [(blocked, True), (ready, False)]:
            tasks.run_command(pl, t.id, command="analyze", expected_version=t.version)
            s.commit()
            tasks.submit_requirements(pl, t.id, spec=spec(open_q), expected_version=t.version,
                                      source="MANUAL_IMPORT", provenance={"source": "seed"})
            s.commit()
        view = tasks.task_view(pl, ready.id)
        tasks.decide_gate(pl, ready.id, gate=Gate.REQUIREMENTS, decision=ApprovalDecision.APPROVED,
                          scope_hash=view["requirements"]["approval_scope_hash"],
                          expected_version=ready.version, reason="Scope agreed with HR")
        s.commit()
        print(f"seeded project PORTAL ({p.id})")


if __name__ == "__main__":
    main()

# Traceability — FRD v1.0

Status of each functional requirement and acceptance test against this codebase. **Done** means implemented and tested at the level the FRD asks for. **Partial** means the foundation is in place and the named gap remains. Update this file with every change that moves a row.

## Delivery stages (PRD §6)

| Stage | State |
|---|---|
| Foundation — identity, RBAC, project setup, state store, audit, sandbox, adapter contracts | **In progress.** All except OIDC identity and the worker sandbox. Exit tests (denied actions, duplicate events) pass. |
| Vertical slice — one ticket through Gemini, requirements gate, Claude, Codex, approved merge | Not started |
| MVP completion — Ollama delegation, costs, notifications, recovery, release records | Not started |
| Pilot release | Not started |

## Functional requirements

| ID | Requirement | Status | Where / gap |
|---|---|---|---|
| FR-01 | Service topology | Partial | Next.js, FastAPI, Postgres, outbox. **Gap:** Celery/Redis jobs, workers, Git broker, object storage, WebSockets (cursor polling today), Ollama bridge. |
| FR-02 | Durable orchestration | Partial | `services/tasks.py`, `services/outbox.py`, `services/leases.py`. **Gap:** one-active-execution and per-project/workspace concurrency limits enforced at dispatch. |
| FR-03 | Sources of truth | Partial | Postgres authority. **Gap:** GitHub reconciliation, content-addressed artifact store, repo context exports. |
| FR-04 | Setup workflow | Partial | Project, repository link via `RepositoryInspector`, readiness checklist. **Gap:** real GitHub App inspector, trusted commands, environment, sandbox baseline run. |
| FR-05 | Agent configuration | Partial | Immutable versions with `expected_version`. **Gap:** connection tests (arrive with adapters). |
| FR-06 | Activation and retirement | Partial | Activation gate with exact missing items. **Gap:** disable, archive, credential revocation. |
| FR-07 | Dashboard | Partial | Project list. **Gap:** stage counts, approvals, blockers, spend, health. |
| FR-08 | Kanban | Done (foundation) | Seven columns, badges not columns, drag and keyboard parity, denied moves explained. |
| FR-09 | Ticket | Partial | Overview, Requirements, Audit, next actions. **Gap:** UX, Code, QA, Runs, Costs tabs. |
| FR-10 | Requirements | Partial | Versions, stable AC IDs, blocking questions, approval with scope. **Gap:** version compare, structured draft editor (JSON import today). |
| FR-11 | Code and QA screen | Not started | |
| FR-12 | Runs and agents screen | Not started | |
| FR-13 | Costs and audit screens | Partial | Audit API with actor/object/action filters. **Gap:** costs, export. |
| FR-14 | Lifecycle and transitions | Done (domain) | `domain/lifecycle.py`. Human-driven triggers wired; worker-driven triggers arrive with the job service. |
| FR-15 | Approval rules | Partial | Requirements gate done; merge scope binding implemented but unreachable until QA exists. **Gap:** release approvals. |
| FR-16 | Enforcement and release boundary | Partial | Deny-by-default at the API. **Gap:** broker and storage enforcement, release state machine. |
| FR-17 | Git workflow | Partial | Branch leases with fencing. **Gap:** Git broker, feature branches, draft PRs, DEV_REVIEW checks. |
| FR-18 | Independent QA and repairs | Partial | QA report contract, coverage check, repair-cycle accounting. **Gap:** QA runs, defects, retest loop. |
| FR-19 | Conditional merge | Not started | |
| FR-20 | Common adapter contract | Done (schemas) | `contracts/__init__.py`. |
| FR-21 | Ollama routing | Done (domain) | `domain/junior_routing.py`. Not yet wired to a worker. |
| FR-22 | Data model | Partial | Identity, projects, repos, agent configs, tasks, specs, ACs, approvals, audit, outbox, idempotency, leases, budgets. **Gap:** runs/attempts, artifacts, QA reports, defects, PRs, releases, usage, reservations, notifications. |
| FR-23 | API and events | Partial | Idempotency, error envelope, concealed 404, cursor events. **Gap:** WebSocket, results, usage, releases, GitHub webhook. |
| FR-24 | Retry and recovery | Not started | Lease heartbeat/TTL constants only. |
| FR-25 | Usage and budget | Partial | Budget caps required for activation. **Gap:** reservations, usage normalization, deltas. |
| FR-26 | Notifications | Not started | |
| FR-27 | Security controls | Partial | Redaction before persistence, append-only triggers, denial audit, dev auth refused in production. **Gap:** OIDC, CSRF, secret manager, sandbox, audit export. |

## Acceptance tests

| AT | Covered by | Level |
|---|---|---|
| AT-01 | `test_activation_denied_without_branch_enforcement` | Full for setup; real GitHub facts pending |
| AT-02 | `test_blocking_question_prevents_approval`, `test_blocking_questions_block_requirements_approval` | Full (no dispatch exists yet) |
| AT-03 | `test_editing_approved_requirements_revokes_and_versions` | Full; job fencing covered by lease release |
| AT-04 | `test_agents_cannot_approve_and_denials_are_audited`, permission matrix tests | API layer; broker layer pending |
| AT-05 | `test_duplicate_command_is_applied_once`, `test_one_active_writer_per_branch` | Commands and leases; PR creation pending |
| AT-06 | — | Needs QA adapter |
| AT-07 | `test_qa_coverage_rejects_missing_and_stale`, `test_qa_verdict_is_deterministic` | Contract level |
| AT-08 | — | Needs merge gate end to end |
| AT-09 | — | Needs Git broker |
| AT-10 | `test_ollama_routing_rejects_protected_or_complex_work`, `test_ollama_patch_scope_is_enforced` | Domain level |
| AT-11 | `test_third_failed_repair_pauses` | Domain level |
| AT-12 | — | Needs reservations |
| AT-13 | — | Needs usage accounting |
| AT-14 | `test_expired_worker_is_fenced` | Lease level |
| AT-15 | `test_cross_project_access_is_concealed` | Full for tickets and projects; artifacts pending |
| AT-16 | `test_cancel_is_terminal_and_preserves_evidence` | Partial; provider completion after cancel pending |
| AT-17 | `test_illegal_jump_leaves_stage_unchanged`, `test_cannot_jump_to_done`, board UI | Full |
| AT-18 | `test_event_cursor_catch_up_has_no_duplicates` | Cursor API; WebSocket pending |
| AT-19 | Permission matrix (`production.execute` never granted) | Partial; releases pending |
| AT-20 | `test_planted_secrets_are_redacted` | Storage redaction; broker secret denial pending |
| AT-21 | `test_crash_mid_relay_redelivers_and_consumer_dedups` | Outbox level |
| AT-22 | — | Needs notifications |
| AT-23 | `test_manual_import_records_provenance` | Full for manual import |
| AT-24 | — | Needs backup/restore drill |

## Next build order

1. **Identity:** OIDC sign-in with HttpOnly cookies and CSRF; scoped workload tokens for workers.
2. **Job service:** Celery on Redis, outbox relay worker, runs/attempts, heartbeats, retry policy (FR-24).
3. **Git broker:** GitHub App inspector, feature branches, draft PRs, path/lease validation, webhook inbox.
4. **Adapters in journey order:** Gemini BA → Claude Code developer → Codex QA, each behind a contract test.
5. **Merge gate end to end:** QA reports, defects, repair loop, conditional merge and reconciliation.

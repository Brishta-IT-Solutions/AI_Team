# Traceability — FRD v1.0

Status of each functional requirement and acceptance test against this codebase. **Done** means implemented and tested at the level the FRD asks for. **Partial** means the foundation is in place and the named gap remains. Update this file with every change that moves a row.

## Delivery stages (PRD §6)

| Stage | State |
|---|---|
| Foundation — identity, RBAC, project setup, state store, audit, sandbox, adapter contracts | **In progress.** All except OIDC identity and the worker sandbox. Exit tests (denied actions, duplicate events) pass. |
| Vertical slice — one ticket through Gemini, requirements gate, Claude, Codex, approved merge | **Mostly done.** Everything up to merge approval runs end to end; the merge itself waits for the Git broker. |
| MVP completion — Ollama delegation, costs, notifications, recovery, release records | Not started |
| Pilot release | Not started |

## Functional requirements

| ID | Requirement | Status | Where / gap |
|---|---|---|---|
| FR-01 | Service topology | Partial | Next.js, FastAPI, Postgres, outbox, worker. **Deviation:** the run queue is Postgres (`SKIP LOCKED`) instead of Celery/Redis for the pilot; Redis is reserved. **Gap:** Git broker, object storage, WebSockets (cursor polling today). |
| FR-02 | Durable orchestration | Partial | `services/orchestrator.py`: one active parent run per ticket (unique index), leases with fencing, dispatch by stage. **Gap:** per-project two-ticket and workspace eight-job limits. |
| FR-03 | Sources of truth | Partial | Postgres authority. **Gap:** GitHub reconciliation, content-addressed artifact store, repo context exports. |
| FR-04 | Setup workflow | Partial | Project, repository link via `RepositoryInspector`, readiness checklist. **Gap:** real GitHub App inspector, trusted commands, environment, sandbox baseline run. |
| FR-05 | Agent configuration | Partial | Immutable versions; live capability and model reported by the worker on the Team panel. **Gap:** stored connection-test results per config version. |
| FR-06 | Activation and retirement | Partial | Activation gate with exact missing items. **Gap:** disable, archive, credential revocation. |
| FR-07 | Dashboard | Partial | Project list. **Gap:** stage counts, approvals, blockers, spend, health. |
| FR-08 | Kanban | Done (foundation) | Seven columns, badges not columns, drag and keyboard parity, denied moves explained. |
| FR-09 | Ticket | Done (pilot) | All eight tabs: Overview, Requirements, UX, Code, QA, Runs, Costs, Audit. |
| FR-10 | Requirements | Partial | Versions, stable AC IDs, blocking questions, approval with scope. **Gap:** version compare, structured draft editor (JSON import today). |
| FR-11 | Code and QA screen | Partial | Branch, head/base, files, self-check output; criteria matrix, suites, defects, evidence freshness. **Gap:** diff view and PR links (need the Git broker). |
| FR-12 | Runs and agents screen | Partial | Team panel and Runs tab: attempt, milestone, duration, logs, model, cost label, junior sub-runs. **Gap:** per-run cancel and resume buttons (ticket-level cancel/retry exist). |
| FR-13 | Costs and audit screens | Partial | Costs tab separates API estimates, plan-covered, local and unknown. Audit filters. **Gap:** project-level cost reports and export. |
| FR-14 | Lifecycle and transitions | Done (domain) | `domain/lifecycle.py`. Human-driven triggers wired; worker-driven triggers arrive with the job service. |
| FR-15 | Approval rules | Partial | Requirements gate done; merge scope binding implemented but unreachable until QA exists. **Gap:** release approvals. |
| FR-16 | Enforcement and release boundary | Partial | Deny-by-default at the API. **Gap:** broker and storage enforcement, release state machine. |
| FR-17 | Git workflow | Partial | `feature/<key>/<id>` worktrees from the recorded base; worker commits and diffs; protected paths; optional push of feature branches only. **Gap:** draft PRs and GitHub branch protection via the broker. |
| FR-18 | Independent QA and repairs | Done (local) | Codex in a separate detached worktree without developer reasoning; exact AC coverage; defects by signature; full retest of each repaired commit; three-cycle pause. |
| FR-19 | Conditional merge | Not started | |
| FR-20 | Common adapter contract | Done | Contracts plus adapters in `apps/worker/aitc_worker/adapters/`. |
| FR-21 | Ollama routing | Done | Routed and patch-checked by the API; patches reviewed and applied by Claude Code, never committed by the junior. |
| FR-22 | Data model | Partial | Adds runs, run events, QA reports (append-only), defects, reservations, workers. **Gap:** artifacts store, PRs, releases, notifications. |
| FR-23 | API and events | Partial | Worker protocol (hello, claim, heartbeat, junior, results), team/runs/QA/brief reads. **Gap:** WebSocket, releases, GitHub webhook. |
| FR-24 | Retry and recovery | Partial | 15 s heartbeats, 90 s run lease, reaping and fencing of lost workers, one repair attempt for invalid structured output, per-role timeouts, cancellation with grace period, client retries with backoff. **Gap:** restart reconciliation of provider side effects. |
| FR-25 | Usage and budget | Partial | Atomic per-project reservations before dispatch, run/ticket/month caps, cumulative-cost deltas, API vs plan vs local vs unknown. **Gap:** 80 % warnings, pricing tables for token-only providers. |
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
| AT-06 | `test_full_team_loop_with_one_repair`, worker `test_the_whole_team_delivers_a_ticket` | Full (local git; stand-in CLIs) |
| AT-07 | `test_incomplete_qa_report_never_reaches_merge_approval`, `test_qa_on_wrong_commit_is_rejected`, contract tests | Full |
| AT-08 | — | Needs merge gate end to end |
| AT-09 | — | Needs Git broker |
| AT-10 | `test_junior_routing_and_patch_checks`, routing unit tests, worker e2e | Full |
| AT-11 | `test_three_failed_repairs_pause`, lifecycle unit test | Full |
| AT-12 | `test_budget_race_admits_only_affordable_work` | Full |
| AT-13 | — | Needs usage accounting |
| AT-14 | `test_lost_worker_is_fenced`, `test_expired_worker_is_fenced` | Full |
| AT-15 | `test_cross_project_access_is_concealed` | Full for tickets and projects; artifacts pending |
| AT-16 | `test_cancel_during_development_keeps_cost_applies_nothing` | Full |
| AT-17 | `test_illegal_jump_leaves_stage_unchanged`, `test_cannot_jump_to_done`, board UI | Full |
| AT-18 | `test_event_cursor_catch_up_has_no_duplicates` | Cursor API; WebSocket pending |
| AT-19 | Permission matrix (`production.execute` never granted) | Partial; releases pending |
| AT-20 | `test_planted_secrets_are_redacted` | Storage redaction; broker secret denial pending |
| AT-21 | `test_crash_mid_relay_redelivers_and_consumer_dedups` | Outbox level |
| AT-22 | — | Needs notifications |
| AT-23 | `test_manual_import_records_provenance` | Full for manual import |
| AT-24 | — | Needs backup/restore drill |

## Next build order

1. **Git broker:** GitHub App, draft PRs per ticket, conditional merge on the approved head, webhook reconciliation. Unlocks merge approval → DONE.
2. **Identity:** OIDC sign-in with HttpOnly cookies and CSRF; scoped workload tokens for workers.
3. **Notifications:** approval requests, failures, paused repairs, budget warnings.
4. **Release records** and the backup/restore drill (AT-19, AT-24).

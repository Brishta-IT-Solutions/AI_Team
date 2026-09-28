# Architecture

## Topology (FR-01)

```
Browser ──► Control API (FastAPI) ──► PostgreSQL ──► outbox ──► queue ──► workers ──► provider adapters
                  ▲                         │                                   │
                  └──── results + leases ◄──┴──────────── Git broker ◄──────────┘
```

Built: Browser, Control API, PostgreSQL (state, outbox and run queue), worker with provider adapters, lease fencing.
Next: Git broker (sole holder of GitHub credentials), object storage for artifacts, WebSocket fan-out.

## The team loop

```
analyze ──► BA run (Gemini) ─────────────► REQUIREMENTS_APPROVAL ──► human approves
                  ▲  manual: Antigravity brief ─► import ─┘                    │
                                                                             ▼
          ┌────────── DEVELOPER run (Claude Code) ◄── budget reserved + branch lease
          │               │  optional: JUNIOR runs (Ollama) → patches → Claude reviews
          │               ▼
          │         DEV_REVIEW (worker-measured diff + approved checks)
          │     fail ─┘      │ pass
          │                  ▼
          └── FIX_REQUIRED ◄─ QA run (Codex, separate worktree, exact commit)
               (≤ 3 cycles)          │ all mandatory criteria and suites pass
                                     ▼
                              MERGE_APPROVAL ──► human (merge via Git broker: next)
```

Workers claim runs with `SELECT … FOR UPDATE SKIP LOCKED`, heartbeat every 15 seconds and hold a 90-second run lease. A silent worker is reaped: its run fails, its branch lease is released, and any late result is refused because its claim token no longer matches. Every result is re-validated by the API and applied as the agent role that produced it.

## Layers in `apps/api/control_api`

| Layer | Rule |
|---|---|
| `domain/` | Pure functions and data: lifecycle, permissions, approval scopes, junior routing. No I/O. |
| `contracts/` | Pydantic models for every boundary where model output enters. `extra="forbid"`, deterministic verdicts. |
| `services/` | One function per command, following the FR-02 transaction. Only place that mutates state. |
| `api/v1/` | HTTP shape only: validation, idempotency wrapper, pagination. No business rules. |
| `db/` | SQLAlchemy models; migrations add what autogenerate can't (triggers, sequences). |

## The mutation transaction (FR-02)

1. `SELECT … FOR UPDATE` the aggregate.
2. Authorize the principal for the specific action (deny by default; non-members get a concealed 404).
3. Compare `expected_version`; mismatch is `409 stale_version`.
4. Gather `GuardFacts` **from persisted state** and `evaluate()` the trigger.
5. Mutate, bump `version`, append an `AuditEvent` (before/after hashes), write an `OutboxEvent`.
6. Store the Idempotency-Key outcome, then commit once.

Delivery is at least once. Consumers call `consume_once(consumer, event_id, handler)` so effects happen at most once. The outbox `seq` doubles as the client event cursor, so reconnecting clients catch up without duplicates.

## Approvals

A decision binds a scope hash (SHA-256 of canonical JSON):

- Requirements: spec version + content hash.
- Merge: spec hash, head SHA, base SHA, QA report ID, policy version.
- Release: merged commit, build digest, environment, staging evidence.

Clients must echo the scope hash they reviewed; the server recomputes it and rejects any mismatch. Changing the underlying tuple revokes prior approvals by appending `REVOKED` rows.

## Stage vs. execution status

`stage` is where the ticket is in the journey; `execution_status` (IDLE, QUEUED, RUNNING, BLOCKED, PAUSED, FAILED) is how work in that stage is going. Retry and resume change only status. Blocked infrastructure never counts as a functional QA failure.

## Leases

One active writer per branch, enforced by a partial unique index. Fencing tokens come from a global sequence, so they only ever increase. The Git broker and result API call `assert_current` before every push or finalization; an expired or released lease can do neither.

# AI Software Team Control Center

A controlled workspace that turns a product request into reviewed, tested code — with every human decision on record.

Gemini (optionally via Antigravity) analyses. Claude Code builds. Codex verifies independently. Local Ollama handles supervised busywork. **Humans approve requirements, merges and releases — agents never can.**

Specs: [`docs/requirements/PRD-v1.0.docx`](docs/requirements/PRD-v1.0.docx) · [`docs/requirements/FRD-v1.0.docx`](docs/requirements/FRD-v1.0.docx)

## Status: Foundation stage

The FRD's build order is *authorization, state guards and audit first*. That layer is in place and tested:

| Area | What exists |
|---|---|
| Lifecycle (FR-14) | Pure, guarded state machine — every FRD transition, terminal stages, MERGING freeze, three-cycle repair pause. Refusals list every unmet prerequisite. |
| Permissions (FR-15/16) | Deny-by-default matrix. Agents and service identities can never approve, merge, push protected branches or touch production. |
| Approvals | Commit-bound scope hashes. Editing approved requirements creates a new version and revokes the approval. Stale scopes are rejected. |
| Integrity | Append-only audit, approvals and spec versions enforced by Postgres triggers. Denials are audited out-of-band so they survive rollback. |
| Delivery | Transactional outbox, consumer dedup, Idempotency-Key replay, durable event cursor, branch leases with fencing tokens. |
| Contracts (FR-20/21) | Strict schemas for BA specs, developer submissions, QA reports (deterministic verdict, exact AC coverage) and Ollama assignments (protected-area routing). |
| Web | Projects, Kanban (drag + keyboard parity, illegal moves explained), ticket view with requirements, decisions, audit and next actions. |

**160 API tests** cover the transition table and acceptance cases AT-01–05, 07, 10, 14, 15, 17, 18, 20, 21, 23 at the foundation level. See [`docs/TRACEABILITY.md`](docs/TRACEABILITY.md) for what is done, partial and not started.

## Run it

Needs Python 3.11+, [uv](https://docs.astral.sh/uv/), Node 22 and Postgres 16.

```bash
cp .env.example .env
make infra     # Postgres + Redis in Docker (or use a local Postgres with user/db "aitc")
make setup     # install API + web dependencies
make seed      # migrate, then create demo users, an active project and three tickets
make api       # http://localhost:8000  (OpenAPI at /docs)
make web       # http://localhost:3000
```

Switch identity with **Acting as** in the header (development sign-in; OIDC replaces it). Try approving `PORTAL-2` as Product Lead — the gate refuses until its blocking question is resolved.

```bash
createdb -O aitc aitc_test && make check   # lint, migration drift check, tests
```

## Layout

```
apps/api     FastAPI control API — domain/ (pure rules), services/ (transactions), contracts/, migrations/
apps/web     Next.js ticket, board and approval surfaces
docs/        Requirements baseline, architecture, traceability
```

Read [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) before changing workflow code, and [`AGENTS.md`](AGENTS.md) if you are an AI agent working in this repo.

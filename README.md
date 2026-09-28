# AI Software Team Control Center

A controlled workspace that turns a product request into reviewed, tested code — with every human decision on record.

Gemini (optionally via Antigravity) analyses. Claude Code builds. Codex verifies independently. Local Ollama handles supervised busywork. **Humans approve requirements, merges and releases — agents never can.**

Specs: [`docs/requirements/PRD-v1.0.docx`](docs/requirements/PRD-v1.0.docx) · [`docs/requirements/FRD-v1.0.docx`](docs/requirements/FRD-v1.0.docx)

## Your AI team

| Member | Role | Runs as | Sign-in you already have |
|---|---|---|---|
| **Gemini** (+ Antigravity) | Business analyst: turns a ticket into stories, rules, testable acceptance criteria and blocking questions | Gemini API, automated | Free API key from [AI Studio](https://aistudio.google.com/apikey). Antigravity works with no key: use **Copy brief for Antigravity**, then import its JSON |
| **Claude Code** | Developer and UX: implements the approved spec on a feature branch, runs the checks, reviews the junior's work | Claude Code CLI, headless, isolated git worktree | Claude **Max**: run `claude setup-token` and paste the token |
| **Codex** | Independent QA: verifies every acceptance criterion on the exact commit, files defects | Codex CLI, separate read-only-intent workspace | ChatGPT **Plus**: `docker compose run --rm worker codex login --device-auth` |
| **Ollama** | Junior: mock data, types, docs, simple tests, renames. Never auth, payments or migrations | Local model on your computer | Nothing. Just have Ollama running with a coding model pulled |
| **You** | Approve requirements and merges. Nothing ships without you | Control Center web app | — |

The loop: **Gemini** drafts → **you** approve → **Claude Code** builds (and may hand donkey work to **Ollama**, then reviews it) → automatic self-check → **Codex** tests independently → failures come back to Claude as defects, up to three repair cycles → **you** approve the merge.

Runs on a Claude or ChatGPT plan are recorded as *covered by plan*, not as API spend. Budgets guard real API spend.

## What is built

| Area | What exists |
|---|---|
| Lifecycle (FR-14) | Guarded state machine for every FRD transition; refusals list each unmet prerequisite. |
| Permissions (FR-15/16) | Deny by default. Agents and services can never approve, merge, push protected branches or touch production. |
| Approvals | Commit-bound scope hashes; editing approved requirements revokes the approval and fences work in flight. |
| Orchestration (FR-02, FR-17–18, FR-24–25) | Runs with frozen envelopes and versioned prompts; atomic budget reservations; branch leases with fencing; lost workers reaped; QA gate on mandatory criteria; defects keyed by failure signature; three-cycle repair pause. |
| Worker (FR-20–21) | Adapters for Gemini, Claude Code, Codex and Ollama. The worker, not the model, commits, diffs and runs the approved checks. Each tool sees only its own credentials. |
| Integrity | Append-only audit, approvals, spec versions and QA reports enforced by database triggers. |
| Web | Team panel, Kanban, ticket view with Requirements, UX, Code, QA, Runs, Costs and Audit. |

**Not yet:** OIDC sign-in, the GitHub broker (so merges are approved in the app but performed by you), notifications and releases. See [`docs/TRACEABILITY.md`](docs/TRACEABILITY.md).

## Run it

**Easiest — one command.** Install [Docker Desktop](https://www.docker.com/products/docker-desktop/), start it, then:

```bash
git clone https://github.com/Brishta-IT-Solutions/AI_Team.git
cd AI_Team
docker compose up --build
```

The first build takes a few minutes. When the log shows `Uvicorn running`, open **http://localhost:3000**. Demo users, a project and three tickets are loaded automatically on the first start. Stop with `Ctrl+C`; `docker compose down -v` also wipes the data.

### Bring the team online

1. `cp .env.example .env` and fill in what you have. Every field is optional; a member without credentials simply shows *not set up* on the board and tells you what it needs.
2. **Claude Code (Max plan):** on your computer run `claude setup-token`, then put the token in `CLAUDE_CODE_OAUTH_TOKEN`.
3. **Codex (ChatGPT plan):** `docker compose run --rm worker codex login --device-auth` and follow the link.
4. **Gemini:** paste a free AI Studio key into `GEMINI_API_KEY`, or skip it and use the Antigravity brief.
5. **Ollama:** keep it running and pull a coding model, e.g. `ollama pull qwen2.5-coder`.
6. `docker compose up --build` again. The four cards at the top of the board turn green as each member comes online.

By default the team works on a small built-in demo project. Set `AITC_REPO_URL` to point it at your own repository; it only ever writes `feature/*` branches.

Switch identity with **Acting as** in the header (development sign-in; OIDC replaces it). Try approving `PORTAL-2` as Product Lead — the gate refuses until its blocking question is resolved. The API and its interactive docs are at http://localhost:8000/docs.

**For development** (hot reload; needs Python 3.11+, [uv](https://docs.astral.sh/uv/), Node 22):

```bash
cp .env.example .env
make infra     # Postgres only, in Docker
make setup     # install API + web dependencies
make seed      # migrate, then load demo data
make api       # http://localhost:8000
make web       # http://localhost:3000
```

Tests: `docker compose exec postgres createdb -U aitc aitc_test && make check`.

## Layout

```
apps/api     FastAPI control API — domain/ (pure rules), services/ (transactions), contracts/, migrations/
apps/web     Next.js ticket, board and approval surfaces
apps/worker  The AI team: adapters for Gemini, Claude Code, Codex and Ollama
docs/        Requirements baseline, architecture, traceability
```

Read [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) before changing workflow code, and [`AGENTS.md`](AGENTS.md) if you are an AI agent working in this repo.

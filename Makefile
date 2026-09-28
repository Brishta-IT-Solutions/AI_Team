API := apps/api
WEB := apps/web
PY  := $(API)/.venv/bin/python

.PHONY: setup infra migrate seed api web worker test lint check

setup:            ## Install API, worker and web dependencies
	cd $(API) && uv venv -q .venv && uv pip install -q -p .venv -e ".[dev]"
	cd apps/worker && uv venv -q .venv && uv pip install -q -p .venv -e ".[dev]" -e ../api uvicorn
	cd $(WEB) && npm install --no-audit --no-fund

infra:            ## Start only Postgres in Docker (for running API and web natively)
	docker compose up -d postgres

migrate:
	cd $(API) && .venv/bin/alembic upgrade head

seed: migrate     ## Demo users, an active project and tickets at several stages
	cd $(API) && .venv/bin/python -m control_api.seed

api:              ## Control API on :8000
	cd $(API) && .venv/bin/uvicorn control_api.main:app --reload --port 8000

web:              ## Web app on :3000
	cd $(WEB) && npm run dev

worker:           ## The AI team, using the CLIs and keys on this computer
	cd apps/worker && AITC_WORK_DIR=$${AITC_WORK_DIR:-$$HOME/.aitc-work} OLLAMA_URL=$${OLLAMA_URL:-http://localhost:11434} .venv/bin/aitc-worker

test:             ## API and worker tests (needs Postgres; uses database aitc_test)
	cd $(API) && .venv/bin/python -m pytest
	cd apps/worker && .venv/bin/python -m pytest

lint:
	cd $(API) && .venv/bin/ruff check . && .venv/bin/alembic check
	cd apps/worker && .venv/bin/ruff check .
	cd $(WEB) && npm run typecheck

check: lint test

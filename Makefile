API := apps/api
WEB := apps/web
PY  := $(API)/.venv/bin/python

.PHONY: setup infra migrate seed api web test lint check

setup:            ## Install API and web dependencies
	cd $(API) && uv venv -q .venv && uv pip install -q -p .venv -e ".[dev]"
	cd $(WEB) && npm install --no-audit --no-fund

infra:            ## Start Postgres (and Redis) in Docker
	docker compose up -d postgres redis

migrate:
	cd $(API) && .venv/bin/alembic upgrade head

seed: migrate     ## Demo users, an active project and tickets at several stages
	cd $(API) && .venv/bin/python -m control_api.seed

api:              ## Control API on :8000
	cd $(API) && .venv/bin/uvicorn control_api.main:app --reload --port 8000

web:              ## Web app on :3000
	cd $(WEB) && npm run dev

test:             ## API tests (needs Postgres; uses database aitc_test)
	cd $(API) && .venv/bin/python -m pytest

lint:
	cd $(API) && .venv/bin/ruff check . && .venv/bin/alembic check
	cd $(WEB) && npm run typecheck

check: lint test

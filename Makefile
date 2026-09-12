# ── rmi-mcp-x402 Makefile ─────────────────────────────────────
# Rug Munch Media fleet standard (from fleet-template/Makefile.python.template)
# Source of truth: https://git.rugmunch.io/RugMunchMedia/rmi-mcp-x402
#
# The T33 MCP server (mcp.rugmunch.io) - exposes 8 tools to AI agents.
# JSON-RPC over SSE - the protocol Claude / Cursor speak.
#
# Layout:
#   app/mcp/server.py             - MCP server entry (FastMCP, uvicorn :8001)
#   app/mcp/x402_mcp_server.py    - implements the 8 tools
#   app/api/                      - FastAPI HTTP routes
#   app/domain/                   - domain logic (catalog, reports, ...)
#   app/routers/                  - additional route modules

SHELL := /bin/bash

# System tool paths (verified 2026-08-21 on this host). Override per-machine.
RUFF ?= ruff
MYPY ?= /usr/local/bin/mypy
PYTEST ?= /usr/local/bin/pytest

# Shadowing guard (feat/coffer-cleanup): the rmi-data-services editable install
# adds a `.pth` mapping `/root` onto sys.path (_editable_impl_rmi_data_services.pth),
# exposing a DIFFERENT top-level `app` package (rmi-data-services). Running
# mypy/pytest via /usr/local/bin sets sys.path[0] to the script dir, NOT the repo
# cwd, so repo `app/` is not otherwise ahead of `/root/app`. PYTHONPATH entries
# are inserted before site-packages addpoints, so `.` (repo cwd) resolves first.
# This does not disturb rmi-data-services — it only guarantees Coffer's `app`
# resolves first when running from this repo.
#
# NOTE / residual: `/root/app` is a *regular* package (has __init__.py), so a bare
# `import app` in a plain interpreter still binds to /root/app even with PYTHONPATH=.
# `make ci` is green today because the spec-conformance tests bind to the
# authoritative `x402` package (repo `app.domain.x402.handler` does not exist yet),
# and mypy resolves the repo tree via explicit_package_bases. A full "repo app
# always wins" guarantee would need `app/__init__.py` (regular-izes the repo pkg;
# out of coffer-cleanup scope) or pruning the rmi-data-services .pth (risks that
# package). Both deliberately left to owner.
export PYTHONPATH := .

.PHONY: help install dev build lint format check test typecheck security \
        precommit commit ci clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install Python deps in editable mode
	pip install -e ".[dev]"

dev: ## Start MCP server (uvicorn :8001, streamable_http_app)
	uvicorn app.mcp.server:app --reload --host 0.0.0.0 --port 8001

build: ## Build Docker image
	docker build -t rmi-mcp-x402 .

lint: ## Lint app/ + tests/ with ruff
	ruff check app/ tests/ 2>/dev/null || ruff check app/

format: ## Format with ruff (writes)
	ruff format app/ tests/ 2>/dev/null || ruff format app/
	ruff check --fix app/ tests/ 2>/dev/null || ruff check --fix app/

check: ## Full check: lint + format + typecheck
	ruff check app/
	ruff format --check app/
	mypy app/ --ignore-missing-imports

typecheck: ## MyPy type check
	mypy app/ --ignore-missing-imports

test: ## Run pytest (spec-conformance + unit suites)
	@pytest tests/ -q 2>/dev/null || pytest tests/ -q

security: ## Security audit (bandit + ruff S-rules)
	bandit -r app/ -q
	ruff check --select S app/

precommit: ## Install pre-commit hooks
	pre-commit install

commit: ## Interactive commit with commitizen
	cz commit || git commit

ci: ## Full CI: ruff (app+tests) + mypy (domain/x402) + pytest, system tools
	$(RUFF) check app tests
	$(MYPY) app/domain/x402 --exclude '/extensions/'
	$(PYTEST) -q

clean: ## Remove build artifacts
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache .mypy_cache .ruff_cache dist build *.egg-info

# ── Deploy (Coffer payment-facilitator) ─────────────────────────────────────
# - run  : local uvicorn, HTTP /v1/x402 on :8001 (fail-closed on unset wallet)
# - up   : docker compose up --build (coffer + redis)  /  down : teardown
.PHONY: run up down

run: ## Run the x402 HTTP facilitator locally (uvicorn, :8001)
	uvicorn --factory app.domain.x402.config:create_app --host 0.0.0.0 --port 8001

up: ## Build + start coffer + redis via docker compose
	docker compose up --build -d

down: ## Stop + remove coffer + redis containers
	docker compose down

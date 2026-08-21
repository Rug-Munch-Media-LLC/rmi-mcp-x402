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

ci: lint typecheck test ## Full CI: lint + typecheck + test

clean: ## Remove build artifacts
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache .mypy_cache .ruff_cache dist build *.egg-info

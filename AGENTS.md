[//]: # (SPDX-License-Identifier: MIT)
[//]: # (Copyright (c) 2026 Rug Munch Media LLC)
# AGENTS.md — RMI MCP x402

> AI agent contract. Read this before touching anything in this repo.

## Status
canonical · owner=crmuncher · last_updated=2026-07-06

## What This Repo Is
Model Context Protocol (MCP) server and x402 payment gateway for Rug Munch Intelligence. Exposes RMI security tools to AI agents (Claude Code, opencode, Cursor) behind a typed interface. Settles per-call x402 payments. Owns the verified badge domain logic.

## Type
`backend` · language=Python 3.12 + FastAPI

## Where It Lives
- Source: `https://git.rugmunch.io/RugMunchMedia/rmi-mcp-x402`
- Deployed: Docker on Talos (target TBD; not yet standalone container)
- Port: TBD

## Built With
- Standards: [git.rugmunch.io/RugMunchMedia/standards](https://git.rugmunch.io/RugMunchMedia/standards)
- Toolchain: [TOOLCHAIN.md](https://git.rugmunch.io/RugMunchMedia/standards/raw/branch/main/TOOLCHAIN.md)
- Conventions: [CONVENTIONS.md](https://git.rugmunch.io/RugMunchMedia/standards/raw/branch/main/CONVENTIONS.md)

## Components
- `app/mcp/` — MCP server and tool manifest
- `app/domain/x402/` — payment middleware, models, service, router
- `app/domain/verified/` — badge domain
- `app/api/v1/` — HTTP API routes

## Dependencies
- RMI backend (security data)
- Qdrant, Redis, Postgres (via rmi-backend)
- x402 facilitator (settlement)

## 🚨 CRITICAL RULES

1. **No nested repos.** Don't commit other complete project trees inside this one.
2. **No secrets.** Never commit `.secrets/`, `.env`, `*.pem`, `*.key`, `*.crt`, wallet keys. Use gopass.
3. **No data blobs.** Don't commit `*.zip`, `*.parquet`, model weights, sqlite files.
4. **No file > 500 lines.** Split or refactor.
5. **No mainnet RPC keys in source.** Use gopass and environment.
6. **x402 canon:** this repo is the MCP actor marketplace; Coffer (x402-coffer) is the facilitator. No gateway repo exists anymore.
7. **Update STATUS.md before committing.** Builders must know where we are.
8. **Read DECISIONS.md before architectural changes.** Don't repeat rejected designs.

## Commands
```bash
make install       # install deps
make dev           # dev server
make test          # tests
make lint          # ruff + format check
make typecheck     # mypy strict
make security      # bandit + gitleaks
make ci            # full pipeline
make commit        # conventional commit
make clean         # remove build artifacts
```

## Architecture
See [ARCHITECTURE.md](ARCHITECTURE.md). Update it when components change.

## Status
See [PRODUCT.md](PRODUCT.md) and [ROADMAP.md](ROADMAP.md).

## Owners
- Primary: crmuncher
- Backup: TBD

## Related Repos
- `rmi-backend/` — security data source
- `walletpress/` — wallet generation
- x402-gateway: ARCHIVED/DELETED 2026-08-25 — do not reference
- See [standards/PRODUCTS.md](https://git.rugmunch.io/RugMunchMedia/standards/raw/branch/main/PRODUCTS.md).
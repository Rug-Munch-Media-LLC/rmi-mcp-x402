<!--
SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC
Part of RMI MCP x402 — https://git.rugmunch.io/RugMunchMedia/rmi-mcp-x402
-->

# Contributing to RMI MCP x402

> MCP server + x402 payment gateway. Python 3.12 + FastAPI.

## TL;DR

```bash
git checkout main && git pull --rebase origin main
git checkout -b feat/my-change
# …make changes…
make lint test            # pre-push gate
make commit               # conventional commits via commitizen
git push origin feat/my-change
# Open a PR on Forgejo (git.rugmunch.io/RugMunchMedia/rmi-mcp-x402)
```

## Repository Model

Single-tenant branch-PR pattern. `feat/*` branches merged to `main` after CI passes.

### Branches

| Branch             | Purpose                                          |
|--------------------|--------------------------------------------------|
| `main`             | Production-ready. Protected. Forgejo-mirrored.   |
| `feat/<scope>`     | New feature or capability.                       |
| `fix/<scope>`      | Bug fix.                                         |
| `chore/<scope>`    | Tooling, deps, formatting, non-functional.       |
| `refactor/<scope>` | Code restructure, no behavior change.           |
| `docs/<scope>`     | Documentation only.                              |

### Commit Convention

Conventional Commits.

```bash
feat(mcp): expose token_risk_score tool
fix(x402): validate payment header against scheme
docs: add ARCHITECTURE.md
chore(deps): bump fastapi to 0.115
```

Allowed types: `feat`, `fix`, `docs`, `refactor`, `style`, `test`, `chore`,
`ops`, `security`, `ci`, `build`, `perf`, `revert`.

## Remotes

| Remote      | URL                                                            | Role                          |
|-------------|----------------------------------------------------------------|-------------------------------|
| `origin`    | `ssh://git@git.rugmunch.io:2222/RugMunchMedia/rmi-mcp-x402.git`| Canonical — Forgejo.          |

## Pre-Push Checklist

Before pushing any branch:

```bash
make lint          # ruff check + format check
make typecheck     # mypy strict
make test          # pytest
make security      # bandit + gitleaks
make ci            # all of the above
```

CI is defined in `.forgejo/workflows/ci.yml`.

## License

By contributing, you agree your work is licensed under the MIT License.

## Further Reading

- `AGENTS.md` — repo contract, owners, commands
- `ARCHITECTURE.md` — module layout, MCP + x402 flow
- `PRODUCT.md` — scope, anti-scope, success criteria
- `../standards/CONVENTIONS.md` — fleet-wide conventions
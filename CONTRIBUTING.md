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
make ci                   # full gate: ruff + mypy-strict + pytest (MUST pass)
make commit               # conventional commits via commitizen
git push origin feat/my-change
# Open a PR on Forgejo (git.rugmunch.io/RugMunchMedia/rmi-mcp-x402)
```

> **Non-negotiable:** `make ci` (lint + typecheck + test) must pass before any
> branch is pushed or merged. Do **not** bypass with `--no-verify`; hooks run
> gitleaks, ruff, mypy, bandit and deptry on push.

## Repository Model

Single-tenant branch-PR pattern. `feat/*` branches merged to `main` after CI passes.

### Branches

| Branch             | Purpose                                          |
|--------------------|--------------------------------------------------|
| `main`             | Production-ready. Protected. Forgejo-mirrored.   |
| `feat/<scope>`     | New feature or capability.                       |
| `fix/<scope>`      | Bug fix.                                         |
| `chore/<scope>`    | Tooling, deps, formatting, non-functional.       |
| `refactor/<scope>` | Code restructure, no behavior change.            |
| `docs/<scope>`     | Documentation only.                              |

### Commit Convention

Conventional Commits.

```bash
feat(mcp): expose token_risk_score tool
feat(x402): register tron facilitator
feat(ext): add payment-identifier gas-sponsoring hook
fix(x402): validate payment header against scheme
docs: add ARCHITECTURE.md
chore(deps): bump fastapi to 0.115
```

Allowed types: `feat`, `fix`, `docs`, `refactor`, `style`, `test`, `chore`,
`ops`, `security`, `ci`, `build`, `perf`, `revert`.

## Adding a new payment network

We advertise (network, asset) pairs in two places that must stay in sync: the
canonical acceptance map and a verifier. **Workflow:**

1. **If this is a spec-defined *scheme* (not just a network),** start from the
   official scheme template:
   `docs/x402-official-spec/scheme_template.md` — define the scheme section
   (name, summary, use cases), then add an appendix entry mirroring the official
   `schemes__<name>/` layout.
2. **Register the network / asset** in
   `app/domain/x402/middleware.py`:
   - add a CAIP-2 entry to `LEGACY_TO_CAIP2` (e.g. `"avalanche": "eip155:43114"`),
   - add decimals to `ASSET_DECIMALS` (override the 6-default),
   - add the (`network` → `[assets]`) pair to `NETWORK_ASSETS`, and
   - confirm a receiving wallet is configured — `build_multi_asset_accepts()`
     only advertises networks that have a `payToAddress`, so set
     `X402_WALLET_<NETWORK>` (see `_pay_to_address`).
3. **Wire up verification** via the facilitator-mesh extension point:
   `FacilitatorRouter.register_facilitator(network, impl)` /
   `register_facilitator(network, impl)` (exported from `app.domain.x402`) —
   `impl` must expose `async verify(payment) -> VerifyResult` and
   `async settle(payment) -> str | None`. This is exactly how **TRON** is
   scaffolded (`tron:mainnet`): structure/price/asset are validated, verification
   returns `verified:false reason:"needs_facilitator"` until you supply a
   facilitator impl.
4. **Update the README** payment-options table and the `/v1/x402/health`
   verification-status output.
5. Add/adjust tests under `tests/` (spec-conformance + unit) and run `make ci`.

> If the network maps to an existing scheme (e.g. another EVM chain), you
> typically only touch the constant maps + facilitator registration — no new
> verifier.

## Adding a new extension

Extensions hook into the payment lifecycle via **lifecycle hooks**. Reference the
official hooks spec: `docs/x402-official-spec/advanced-concepts__lifecycle-hooks.md`.

The server lifecycle hooks are: `onBeforeVerify`, `onAfterVerify`,
`onVerifyFailure`, `onBeforeSettle`, `onAfterSettle`, `onSettleFailure`,
`onVerifiedPaymentCanceled`. Multi-settle flows supply a `phase`
(`before-handler` / `after-handler` / `cancel`) so hooks that must not fire twice
branch on `context.phase`.

**Workflow (`feat(ext): <name>`):**

1. Model the extension after an existing one (see the official
   `extensions__payment-identifier.md` and `extensions__overview.md` for the
   declaration shape). The repo uses extension keys such as `payment-identifier`,
   `eip2612-gas-sponsoring`, `erc20-approval-gas-sponsoring`.
2. Hook the relevant lifecycle points with **enrich hooks**
   (`enrichDeclaration`, `enrichPaymentRequiredResponse`,
   `enrichSettlementResponse`) so the 402 body and settlement response are
   spec-shaped.
3. Add tests proving the hook runs on the right `phase` and that a side-effect
   that must run once still runs once across a two-phase settle.
4. Document the extension key in `README.md`'s **Extensions** table and list it
   in the health output.
5. `make ci`, then conventional-commit `feat(ext): ...`.

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
make ci            # all of the above (BLOCKING)
```

CI is defined in `.forgejo/workflows/ci.yml`.

## License

By contributing, you agree your work is licensed under the MIT License.

## Further Reading

- `AGENTS.md` — repo contract, owners, commands
- `ARCHITECTURE.md` — module layout, MCP + x402 flow
- `PRODUCT.md` — scope, anti-scope, success criteria
- `docs/x402-official-spec/scheme_template.md` — scheme definitions
- `docs/x402-official-spec/advanced-concepts__lifecycle-hooks.md` — hooks
- `../standards/CONVENTIONS.md` — fleet-wide conventions

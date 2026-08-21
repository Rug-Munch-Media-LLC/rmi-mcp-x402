<!--
SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC
Coffer — the self-hosted x402 payment facilitator
-->

# Contributing to Coffer

> Self-hosted x402 payment facilitator. Python 3.12 + FastAPI. Accept
> micropayments in one line, settle on your own keys.

## TL;DR

```bash
git checkout main && git pull --rebase origin main
git checkout -b feat/my-change
# …make changes…
make lint test            # pre-push gate
make ci                   # BLOCKING: ruff + mypy-strict + pytest MUST pass
git push origin feat/my-change
# Open a PR (Forgejo: git.rugmunch.io/RugMunchMedia/rmi-mcp-x402)
```

> **Non-negotiable:** `make ci` (lint + typecheck + test) must pass before any
> branch is pushed or merged. Do **not** bypass with `--no-verify`; hooks run
> gitleaks, ruff, mypy, bandit and deptry on push.

## Repository model

Single-tenant branch-PR. `feat/*` merges to `main` after CI passes.

| Branch | Purpose |
|--------|---------|
| `main` | Production-ready. Protected. Mirrored. |
| `feat/<scope>` | New feature or capability. |
| `fix/<scope>` | Bug fix. |
| `chore/<scope>` | Tooling, deps, formatting. |
| `refactor/<scope>` | Code restructure, no behavior change. |
| `docs/<scope>` | Documentation only. |

Conventional commits:

```bash
feat(x402): register tron facilitator
feat(ext): add payment-identifier gas-sponsoring hook
feat(assets): advertise USDC on base
fix(x402): validate payment header against scheme
docs: add ARCHITECTURE.md
chore(deps): bump fastapi to 0.115
```

Allowed types: `feat`, `fix`, `docs`, `refactor`, `style`, `test`, `chore`,
`ops`, `security`, `ci`, `build`, `perf`, `revert`.

## Adding a new payment network / asset

Payment surfaces live in the canonical acceptance tables
(`app/domain/x402/models.py` → `SUPPORTED_NETWORKS` / `SUPPORTED_ASSETS`) and
must stay in sync with the verifier. **Workflow:**

1. **If this is a spec-defined *scheme* (not just a network),** start from the
   official template: `docs/x402-official-spec/scheme_template.md` — define the
   scheme section (name, summary, use cases), then add an appendix entry under
   `schemes__<name>/`.
2. **Add the asset** to `SUPPORTED_ASSETS` (keyed by CAIP-2 network → symbol):
   - `_entry(asset, network, name, contract, decimals, verification, kind)`,
   - set `decimals` (default 6 for stablecoins, 18 for native/WETH/ARB/POL,
     9 for SOL),
   - `verification` ∈ `eip3009 | permit2 | native | spl | trc20`,
     `kind` ∈ `erc20 | native | spl | trc20`, and
   - confirm a receiving wallet is configured (`X402_WALLET_<NETWORK>`) — a
     network is only advertised in `accepts[]` once it has a `payToAddress`.
3. **Wire up verification** via the facilitator extension point
   `FacilitatorRouter.register_facilitator(network, impl)` (exported from
   `app.domain.x402`). `impl` must expose `async verify(payment) -> VerifyResult`
   and `async settle(payment) -> str | None`. This is exactly how **TRON** is
   scaffolded: structure/price/asset validated, `verified:false:
   "needs_facilitator"` until you supply an impl.
4. **Update the README** payment-options matrix and the `/v1/x402/health`
   `VERIFICATION_STATUS` output — and keep it honest (live vs scaffolded).
5. Add tests under `tests/` (spec-conformance + unit) and run `make ci`.

> Already an EVM chain or SPL asset? You typically only touch the constant maps
> + facilitator registration — no new verifier.

## Adding a new extension

Extensions hook the payment lifecycle via **lifecycle hooks**. Reference
`docs/x402-official-spec/advanced-concepts__lifecycle-hooks.md`.

Server hooks: `onBeforeVerify`, `onAfterVerify`, `onVerifyFailure`,
`onBeforeSettle`, `onAfterSettle`, `onSettleFailure`,
`onVerifiedPaymentCanceled`. Multi-settle flows supply a `phase`
(`before-handler` / `after-handler` / `cancel`) so a once-only side effect stays
once.

**Workflow (`feat(ext): <name>`):**

1. Model after an existing extension (`app/domain/x402/extensions/` has
   `payment_identifier`, `gas_sponsoring`, `schemes`). Official shapes in
   `extensions__payment-identifier.md` and `extensions__overview.md`.
2. Hook the lifecycle points with **enrich hooks** (`enrichDeclaration`,
   `enrichPaymentRequiredResponse`, `enrichSettlementResponse`) so the 402 body
   and settlement response stay spec-shaped.
3. Add tests proving the hook fires on the right `phase` and a once-only side
   effect runs exactly once across a two-phase settle.
4. Declare it in `register_all_extensions()` and list it in the README
   **Extensions** table + health output.
5. `make ci`, then commit `feat(ext): ...`.

## Remotes

| Remote | URL | Role |
|--------|-----|------|
| `origin` | `ssh://git@git.rugmunch.io:2222/RugMunchMedia/rmi-mcp-x402.git` | Canonical — Forgejo. |

## Pre-push checklist

```bash
make lint          # ruff check + format check
make typecheck     # mypy strict
make test          # pytest
make security      # bandit + gitleaks
make ci            # all of the above (BLOCKING)
```

CI is defined in `.forgejo/workflows/ci.yml`.

## License

By contributing you agree your work is licensed under the MIT License.

## Further reading

- `AGENTS.md` — repo contract, owners, commands
- `ARCHITECTURE.md` — module layering, MCP + 402 flow
- `README.md` — value prop, payment matrix, examples
- `docs/x402-official-spec/scheme_template.md` — scheme definitions
- `docs/x402-official-spec/advanced-concepts__lifecycle-hooks.md` — hooks
- `../standards/CONVENTIONS.md` — fleet-wide conventions

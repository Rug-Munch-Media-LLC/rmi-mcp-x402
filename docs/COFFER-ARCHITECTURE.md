# Coffer — Architecture

> **Coffer** — "the self-hosted x402 payment facilitator. Your HTTP treasury,
> sovereign and open." Package name: `coffer`.

Coffer is a spec-compliant **x402** (`HTTP 402 Payment Required`) payment
surface that is *both* a resource server (gate any FastAPI route / MCP tool
behind a micropayment) *and* a facilitator mesh (route settlement across
Coinbase, PayAI, Cloudflare — or your own on-chain self-hosted facilitator).
Everything ships MIT-licensed so the **entire** payment rail can run in your
own infrastructure.

This document pins the **modular layering** and the **public API surface**.
It is the single reference for "what imports what" and "what may users import."

---

## 0. Brand vs. spec vocabulary

Rebranding applies **only** to our product and the legacy `pry` naming. The
official x402 protocol terms are the *spec*, not our brand, and are kept
verbatim everywhere:

| Kept (x402 spec terms)              | Rebranded (our product / legacy)          |
|-------------------------------------|-------------------------------------------|
| `x402Version`, `X-PAYMENT`          | product name `pry` → **Coffer**           |
| `accepts`, `PAYMENT-REQUIRED`       | package `rmi-mcp-x402` → **`coffer`**     |
| `FacilitatorRouter`, `exact` scheme | `PRY_X402_*` env aliases → removed        |
| `verify` / `settle` contracts       | `pry-default-wallet` → `coffer-unconfigured-wallet` |
| CAIP-2 network ids, EIP-3009/7702   | `pry.dev` URLs → `coffer.example` (dropped/placeholder) |

`coffer.example` uses the IANA-reserved `.example` TLD: it is a placeholder
that can never resolve to a live host, so no accidentally-real billing URL
escapes into a `PaymentRequired` body.

---

## 1. The layers

```
                     ┌────────────────────────────────────────────┐
   users / agents →  │  coffer/            (public facade)        │
                     │  import coffer; @coffer.require_payment() │
                     └───────────────┬────────────────────────────┘
                                     │ re-exports (precedence: domain > spec)
        ┌────────────────────────────┼───────────────────────────────┐
        ▼                            ▼                               ▼
┌─────────────────────┐  ┌───────────────────────────┐  ┌─────────────────────┐
│ app/domain/x402/    │  │ app/domain/x402/          │  │ vendor/x402/        │
│ core domain         │  │ extensions/               │  │ vendored x402 spec  │
│ (product layer)     │  │ (optional capabilities)   │  │ (x402-py reference) │
└──────────┬──────────┘  └─────────────┬─────────────┘  └──────────┬──────────┘
           │                           │                           │
           └───────────────┬───────────┴───────────────────────────┘
                           ▼
               app/mcp/manifest.py  (MCP tool manifest schema)
               exposed via coffer.mcp (typed, described tools)
```

### 1.1 Core domain — `app/domain/x402/`
Coffer's **product layer**: the spec-canonical 402 offer / verify / settle
flow, wire models, server-authoritative pricing, and facilitator routing.
This is the security-critical payment-verification layer. It does **not**
import from `vendor/`; it mirrors the wire contract of the reference
implementation so the product owns its own destiny.

Key modules: `models.py` (wire models + network/asset tables), `service.py`
(FacilitatorRouter + pricing + honest verification status), `middleware.py`
(spec-compliant 402 gating + `require_payment`), `router.py` (`/v1/x402`
HTTP surface), `tools/` + `tools_router.py` (paid intelligence tools).

### 1.2 Extensions — `app/domain/x402/extensions/`
Composable, **optional** capabilities layered on the core exact/Permit2 flow
(spec: `docs/x402-official-spec/extensions__overview.md`):

- `payment_identifier` — idempotency
- `gas_sponsoring` — EIP-2612 + ERC-20 gasless Permit2 approval
- `schemes` — `upto` + `batch-settlement`

All on-chain settlement steps are marked `scaffolded:needs_facilitator` and
never claim to be verified.

### 1.3 Vendored spec — `vendor/x402/`
A **vendored copy** of the `x402-py` reference implementation (the source of
truth for the spec, per `docs/X402-SPEC-CONTRACT.md` §G10). It is activated
as the top-level `x402` name (see `vendor/README.md`): a venv `.pth` puts
`vendor/` at the front of `sys.path` so `import x402` binds here, and
`coffer._spec` reproduces that binding when the `.pth` is absent.

**Env hygiene (this rebrand):** every setting reads the canonical `X402_*`
env var only. All `PRY_X402_*` backward-compat aliases have been removed.

### 1.4 MCP tool manifest — `app/mcp/manifest.py` + `coffer.mcp`
Coffer is **MCP-native**. Every paid tool is described by a typed
`MCPToolManifest` (semantic `{server}:{tool}` name, semver version, JSON
Schema input, auth scope). `coffer.mcp.coffer_tool_manifest()` derives the
manifest **from the live paid-tools router**, so tool metadata can never
drift from the HTTP surface agents actually call.

---

## 2. Public API surface — `import coffer`

The facade (`coffer/__init__.py`) is the **only** import surface users need.
It proxies the domain layer and the vendored spec. **On a name collision the
domain (product) layer wins**; spec-only primitives are still re-exported,
and the entire vendored module stays reachable as `coffer.spec`.

```python
import coffer

@coffer.require_payment()
async def paid_endpoint(request):
    return {"data": "you paid!"}
```

### 2.1 From the core domain (`app/domain/x402`)
- Offer builders: `build_payment_required`, `build_multi_asset_accepts`,
  `payment_required_body`, `payment_required_headers`
- Facilitator routing: `FacilitatorRouter`, `get_facilitator_router`,
  `register_facilitator`
- Wire models: `PaymentRequirement`, `PaymentOption`, `PaymentReceipt`,
  `PaymentResponse`, `PaymentFacilitator`, `VerifyResult`
- Tables & pricing: `SUPPORTED_ASSETS`, `SUPPORTED_NETWORKS`,
  `VERIFICATION_STATUS`, `X402_PRICING`, `X402_VERSION`, `asset_to_atomic`,
  `flatten_supported_assets`, `resolve_price_usd`, `validate_client_amount`
- MCP tool-catalog models: `ToolCatalog`, `ToolCatalogEntry`, `ToolPricing`,
  `X402Tier`
- Middleware / gating: `require_payment`, `PaymentStore`, `X402Middleware`,
  `get_default_store`, `set_default_store`, `caller_key_from_request`,
  `PAYMENT_VALIDITY_SECONDS`, `HEADER_*` constants, `x402_router`
- Module alias: `coffer.extensions`

### 2.2 From the vendored spec (`vendor/x402`) — spec-only names
`X402Handler`, `SelfFacilitator`, `NonceStore`, `SettleErrorCode`,
`SettleOutcome`, `X402Scheme`, `X402Network`, `X402Asset`,
`X402_UNCONFIGURED_WALLET`, `set_secret_resolver`, `create_batch_payment`,
`is_batch_paid`, `verify_batch_payment`, `verify_solana_payment`,
`SOLANA_USDC_MINT`, `DEFAULT_SOLANA_RPC_URL`, `PaymentRequired` (exception).

The full vendored module is `coffer.spec`; its per-route decorator is
`coffer.spec.middleware.require_payment` (framework-adapter variant).

### 2.3 MCP-native discovery (`coffer.mcp`)
`MCPToolManifest`, `MCPServerManifest`, `coffer_tool_manifest()`,
`tool_price_usd()`, plus `COFFER_SERVER` / `COFFER_AUTH_SCOPE` constants.

---

## 3. Spec import path — `[REVIEW]` flags

The vendored spec is written as a **top-level** `x402` package (its
submodules do `from x402 import ...`). `coffer/_spec.py::resolve_spec_module`
makes that name resolve to the vendored copy:

1. `sys.modules["x402"]` already holds the vendored spec → reuse.
2. `import x402` resolves to the vendored spec (venv `.pth`) → use it.
3. Otherwise load `vendor/x402` from the checkout and register it as
   `sys.modules["x402"]`, purging any stale submodules first.

`coffer.SPEC_SOURCE` reports which path was taken: `"top-level"` (1/2) or
`"facade-loaded"` (3).

**`[REVIEW]` Shadowing trade-off.** In case 3 the facade replaces
`sys.modules["x402"]` process-wide — the same trade-off the venv `.pth` makes
at interpreter startup, mandated by the single-vendored-spec-library design.
`app/routers/*` import the official Coinbase SDK inside `try/except
ImportError` and degrade to `SDK_AVAILABLE = False` when the vendored copy
wins. Do **not** rely on both the official SDK and the vendored spec in one
process.

**`[REVIEW]` Facade-vs-vendured import path.** Product code should import
from `coffer` (never `vendor.x402` directly, and never assume `x402` is the
official SDK). Tests that must bind a specific spec backend should keep using
`tests/conftest.py` resolution (`app.domain.x402` first, then a reference
`x402-py`), which is independent of the facade.

---

## 4. Configuration (canonical env vars only)

All settings read `X402_*` (no legacy fallbacks after this rebrand):

| Var | Meaning | Default |
|-----|---------|---------|
| `X402_WALLET` | receiving wallet | `coffer-unconfigured-wallet` (fails closed) |
| `X402_NETWORK` / `X402_ASSET` | default network / asset | `base` / `USDC` |
| `X402_NETWORK_WALLETS` via `X402_WALLET_<CHAIN>` | per-network receiving wallets | falls back to `X402_WALLET` |
| `X402_SELF_FACILITATE` (+`_KEY`,`_RPC`) | self-hosted settlement | off |
| `X402_OFFLINE` (+`X402_DEBUG`) | dev-only verify/settle bypass | off |
| `X402_ORACLE_URL` / `_SOURCE` / `_TTL` | live price oracle | static defaults |

`coffer-unconfigured-wallet` is a **sentinel**: verification fails closed
while it is in effect, so an unconfigured deployment can never accept money
into a placeholder.

---

## 5. Packaging

`pyproject.toml` ships `name = "coffer"` and packages `app/`, `coffer/`, and
`vendor/`. Dependencies are unchanged (set by agent A, wave-2). The `coffer/`
facade + `coffer/py.typed` marker provide a typed public surface.

## 6. Invariants (do not break)

1. **Spec terms are never rebranded** — only `pry` product/legacy naming.
2. **Domain wins** on facade name collisions; `coffer.spec` keeps the full
   vendored module reachable.
3. **No `PRY_X402_*` env aliases** anywhere; canonical `X402_*` only.
4. **Server-authoritative pricing** — never trust the client's claimed amount
   (compare in atomic units).
5. **Honest verification status** — scaffolded chains never report `verified`.
6. **Fail closed** while the wallet is the unconfigured sentinel.

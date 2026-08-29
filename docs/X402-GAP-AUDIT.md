# X402 Gap Audit — rmi-mcp-x402 vs the official x402 spec

> Status: **canonical** · owner: RugMunch Engineering · date: 2026-08-21
> Scope: `app/domain/x402/*` + `app/routers/x402_*.py` in this worktree
> (`feat/x402-models`). Companion to `docs/X402-SPEC-CONTRACT.md` (G1–G10).
>
> Authoritative references (local, do not fetch from the web):
> - `docs/x402-official-spec/x402-specification-v1.md` (v1 wire shape)
> - `docs/x402-official-spec/x402-specification-v2.md` (protocol v2)
> - `docs/x402-official-spec/schemes__exact__scheme_exact.md`
> - `docs/x402-official-spec/transports-v1__http.md`
> - `<x402-py>/x402/scheme.py` — canonical Python reference
>   (`build_payment_required`, `SUPPORTED_ASSETS`, pricing helpers)
>
> Line numbers for `app/domain/x402/models.py` marked **(HEAD)** refer to the
> pre-rewrite file (`git show HEAD:app/domain/x402/models.py`); the rewrite
> lands on branch `feat/x402-models` and closes the models-layer part of each
> gap below.

## Summary table

| # | Gap | Evidence (file:line) | Spec violated | Models-layer status |
|---|-----|----------------------|---------------|---------------------|
| G1 | Non-spec 402 body model `PaymentRequest{tool,chain,user_address}` | `models.py:63-75` (HEAD) | v1 spec §5.1 (`x402Version`,`accepts[]`) | **FIXED** — `PaymentRequirement`/`PaymentOption` |
| G2 | `X-Payment: base64({tx_hash,signature})` instead of spec headers | `middleware.py:314-328, 529-540` | transports-v1 §Payment Payload (X-PAYMENT = base64 PaymentPayload) | open (middleware agent) |
| G3 | Hand-rolled `create_invoice` w/ placeholder wallet + HMAC "signature" | `middleware.py:286-311` | v1 spec §5.1.2 (`payTo`, atomic `maxAmountRequired` STRING) | open (middleware agent) |
| G4 | Bespoke USDC Transfer-log scanner instead of facilitator verify/settle | `middleware.py:360-461` | v1 spec §7.1/§7.2 facilitator interface | open (middleware agent) |
| G5 | Bare network names ("base", "solana") | `middleware.py:54-58, 302`; `models.py:69` (HEAD); `service.py:68-70` | v2 spec §11.1 CAIP-2 `namespace:reference` | **FIXED in models** — CAIP-2 validated + legacy normalised |
| G6 | No server-authoritative pricing / underpayment rejection | `middleware.py:377, 490-547`; `router.py:135-155` | contract §4 (`resolve_price_usd`, `validate_client_amount`, atomic compare) | open (middleware agent) |
| G7 | No multi-asset `accepts[]` surface | `middleware.py:54-58` (USDC on 3 chains only) | contract §2.2 (`networks`, `supported_assets`); x402-py `build_multi_asset_accepts` | **FIXED in models** — `NetworkInfo`/`SupportedAsset` + optional tables |
| G8 | No caller-bound, TTL'd payment-id validity | `middleware.py:159-180` (tx_hash nonce only) | contract §2.3 (X-Payment-ID, caller-bound, PAYMENT_VALIDITY_SECONDS) | open (middleware agent) |
| G9 | No honest scaffolded/live verification status | `__init__.py:14-24` (health = tool count only) | contract §6 (`VERIFICATION_STATUS` via `/v1/x402/health`) | open (middleware/router agent) |
| G10 | x402 reimplemented from scratch instead of delegating to the SDK | `middleware.py` (all); contrast `routers/x402_verifier.py:27-50` (already SDK) | contract §7 preferred fix (vendor/delegate x402-py) | open (middleware agent) |

## Gap details

### G1 — 402 body model is not the spec shape

**Current (HEAD):** `models.py:63-75` defines `PaymentRequest{tool, chain,
user_address}` and `models.py:78-86` `PaymentReceipt{tool, chain, tx_hash}`.
Neither exists in the x402 protocol.

**Spec:** v1 spec §5.1.1–5.1.2 (`x402-specification-v1.md:74-124`) — the 402
response carries `PaymentRequirementsResponse`: `{x402Version, error,
accepts[]}` where each `accepts[]` entry is `{scheme, network,
maxAmountRequired, asset, payTo, resource, description, mimeType,
maxTimeoutSeconds, extra}`.

**Fix (models layer, done):** `PaymentRequirement{x402Version=1, error?,
accepts: list[PaymentOption] (min 1), networks?, supported_assets?}` with
`PaymentOption` matching `x402-py::build_payment_required`'s emitted keys
(`payToAddress` per the fleet wire contract §2.2). Non-spec
`PaymentRequest` deleted; `PaymentReceipt` kept only as an alias of the new
spec-conformant `PaymentResponse` for import compatibility.

### G2 — Payment headers are non-spec

**Current:** `middleware.py:314-328` decodes an `X-Payment` header carrying
`base64({"tx_hash", "signature"})`; `middleware.py:529-540` gates on it.
No `PAYMENT-REQUIRED` / `X-Payment-Required` / `X-Payment-Options` response
contract, no `X-PAYMENT` PaymentPayload, no `X-PAYMENT-RESPONSE` receipt.

**Spec:** transports-v1__http.md — server signals 402 with the
`PaymentRequirementsResponse` JSON body (§Payment Required Signaling, lines
7-41); client pays via `X-PAYMENT` = base64 `PaymentPayload`
(`{x402Version, scheme, network, payload{signature, authorization}}`, lines
46-48); server returns the settlement receipt in `X-PAYMENT-RESPONSE`
(lines 87-89). Fleet contract §2.1 additionally mandates the base64
`PAYMENT-REQUIRED` header + `X-Payment-Required: true` +
`X-Payment-Options: x402`.

### G3 — Invoice creation is a placeholder, not a payment requirement

**Current:** `middleware.py:286-311` `create_invoice()` emits
`pay_to: "0xRMI_PLATFORM_WALLET"` (line 301 — a literal placeholder, not a
wallet), `amount_wei` hard-coded at ETH=$3000 (line 300), and an HMAC-SHA256
"signature" over the invoice (lines 306-310). None of this is x402.

**Spec:** v1 spec §5.1.2 — `accepts[]` entries need a real recipient
(`payTo`/`payToAddress`) and `maxAmountRequired` as an atomic-unit **string**;
schemes/exact ("Critical Validation Requirements") — the transferred amount
MUST equal `maxAmountRequired` exactly, so the advertised amount must be real
atomic units, not USD floats or placeholder wei.

### G4 — Hand-rolled on-chain verification instead of the facilitator contract

**Current:** `middleware.py:360-461` `verify_payment_on_chain()` scans ERC-20
Transfer logs itself across `middleware.py:61-77` public RPCs (USDC only,
ethereum/base/arbitrum).

**Spec:** v1 spec §7.1/§7.2 — verification and settlement are the
facilitator's job (`POST /verify` read-only, `POST /settle` state-committing,
`GET /supported`). Fleet contract §5 defines `FacilitatorRouter`
(coinbase → payai → cloudflare) and self-facilitation (EIP-3009/EIP-7702,
SPL RPC verifier). The domain middleware must delegate to that contract —
`app/routers/x402_verifier.py:27-50` already imports the official SDK
(`x402.server.x402ResourceServer`, `x402.mechanisms.evm.exact`), proving the
dependency is available.

### G5 — Bare network names instead of CAIP-2

**Current:** `middleware.py:54-58` keys USDC by `"ethereum"/"base"/
"arbitrum"`; `middleware.py:302` invoices `"chain": "base"`;
`models.py:69` (HEAD) defaults `chain="solana"`; `service.py:68-70` lists
facilitator chains as bare names.

**Spec:** v2 spec §11.1 (`x402-specification-v2.md:615-630`) — networks are
CAIP-2 `namespace:reference` (`eip155:8453`, `solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp`,
...); fleet contract §3 fixes the fleet ids (`solana:mainnet`,
`tron:mainnet`) and says "always use the CAIP-2 id, accept legacy names".

**Fix (models layer, done):** every `network` field validates as CAIP-2
(`^[-a-z0-9]{3,8}:[-_a-zA-Z0-9]{1,32}$`) and normalises the 8 known legacy
names (`CAIP2_NETWORK_IDS`) — anything else is rejected at construction.

### G6 — No server-authoritative pricing enforcement

**Current:** `middleware.py:377` computes expected atoms from a float passed
through `require_payment`; there is no `resolve_price_usd` /
`validate_client_amount` equivalent, no atomic comparison protecting against
float drift, no explicit underpayment-refuse/overpayment-allow semantics;
`router.py:135-155` `/verify` accepts any decodable header for any priced
tool.

**Spec:** fleet contract §4 — pricing is server-authoritative ("NEVER trust
client amount"): `resolve_price_usd(operation, network, asset)` with
per-network/per-asset overrides, comparison in atomic units, underpayment
refused, overpayment allowed, non-finite/negative refused. Reference impl:
`x402-py scheme.py:860-928`.

### G7 — Single-asset acceptance surface

**Current:** `middleware.py:54-58` accepts only USDC on 3 EVM chains; no
`networks` / `supported_assets` tables, no per-asset quoting.

**Spec:** fleet contract §2.2 + §3 — the 402 body advertises every accepted
(network, asset) pair; multi-asset mode attaches top-level `networks`
(SUPPORTED_NETWORKS) and `supported_assets` (flattened table). Reference:
`x402-py scheme.py:931-979` (`build_multi_asset_accepts`).

**Fix (models layer, done):** `PaymentRequirement.networks:
list[NetworkInfo]?` and `supported_assets: list[SupportedAsset]?` model the
two tables exactly as `x402-py` emits them (`{network,name,family,chain}` /
`{asset,network,name,contract,decimals,verification,kind}`).

### G8 — No caller-bound payment-id validity

**Current:** `middleware.py:159-180` burns a nonce keyed by `tx_hash` only.
There is no verified-payment-id replay (`X-Payment-ID`), no caller binding
(client IP / caller_key), and no `PAYMENT_VALIDITY_SECONDS` TTL.

**Spec:** fleet contract §2.3 — verified ids stay valid for
`PAYMENT_VALIDITY_SECONDS` and are caller-bound; replay from a different
caller is rejected. v1 spec §10.1 (replay prevention) covers the on-chain
layer (EIP-3009 nonce + time windows); the server-side id layer is additive.

### G9 — No honest verification status

**Current:** `__init__.py:14-24` health reports only the tool count. Nothing
distinguishes live verification (EVM, Solana) from scaffolded (TRON).

**Spec:** fleet contract §6 — `VERIFICATION_STATUS` per chain family
(`live` / `scaffolded:needs_facilitator`) must be exposed via
`/v1/x402/health`; scaffolded chains are never reported as live. Reference:
`x402-py scheme.py:430-452, 689-701`.

### G10 — Reimplementation instead of delegation

**Current:** `middleware.py` (572 lines) reimplements invoicing, header
codec, RPC verification, receipts from scratch, while
`app/routers/x402_verifier.py:27-50` already requires the official SDK
(`pip install 'x402[fastapi]'`, fail-fast).

**Spec:** contract §7 — preferred fix is to vendor `x402-py` (sibling repo
`<x402-py>`) or delegate to its public API / the official SDK,
closing G1–G10 at once. The models rewrite in this branch is deliberately
SDK-shaped so the middleware can delegate without another model churn.

## The router duplication mess — `app/routers/x402_*.py`

`app/routers/` contains **27** files, all `x402_*.py` (nothing else):

```
x402_advanced_tools      x402_alpha_revenue_tools x402_alpha_tools
x402_analytics           x402_bridge_health       x402_catalog
x402_checkout            x402_contract_upgrade_monitor
x402_cross_chain_whale   x402_dashboard           x402_databus_fallback
x402_databus_tools       x402_deployer_history    x402_dex_pool_manipulation
x402_enforcement         x402_enrichment          x402_forensic_tools
x402_insider_network     x402_institutional_tools x402_launch_fairness
x402_middleware          x402_premium_tools       x402_settle_api
x402_subscriptions       x402_token_watch         x402_tools
x402_verifier
```

Why this is a problem:

1. **Ownership collision.** This repo is the MCP intelligence service; the
   dedicated **x402-gateway** owns the payment protocol surface (AGENTS.md
   fleet map: "rmi-mcp-x402 — Canonical x402 gateway"). 27 parallel payment
   routers here re-implement settlement, checkout, enforcement and middleware
   that belong in one gateway code path.
2. **Config drift.** Each copy carries its own USDC table / RPC list /
   facilitator URL: `x402_checkout.py:19-50` (USDC_ADDRESSES, BLOCKSCOUT_URLS,
   EVM_RPC_URLS), `x402_settle_api.py:25-28` (USDC_BASE, PAY_TO, BASE_RPC),
   `x402_verifier.py:57-60` (FACILITATOR_URL, SUPPORTED_NETWORKS),
   `app/domain/x402/middleware.py:44-77` — four+ divergent copies of data
   that `x402-py::SUPPORTED_ASSETS` defines once.
3. **Spec drift.** Some files already speak SDK x402 (`x402_verifier.py`),
   others speak the bespoke `X-Payment`/invoice protocol
   (`x402_middleware.py`, domain middleware) — clients cannot get a
   consistent 402 contract across the fleet.

**Recommendation:** consolidate. Move protocol logic (verify/settle/checkout/
enforcement/health) into the x402-gateway backed by `x402-py` (or the
official SDK already imported by `x402_verifier.py`); shrink this repo to
(a) the domain models in `app/domain/x402/models.py`, (b) pricing/catalog
endpoints, (c) a thin delegation client. Delete or archive the 27 router
copies once the gateway serves the same routes. Interim: freeze new features
in `app/routers/x402_*.py`.

## Cross-agent interface contract (models layer)

Other agents rewriting `middleware.py` / `service.py` / `router.py` /
`tools_router.py` should import from `app.domain/x402.models`:

```python
from app.domain.x402.models import (
    CAIP2_NETWORK_IDS,      # legacy name -> CAIP-2 map
    NetworkInfo,            # networks[] table row
    PaymentOption,          # accepts[] entry (wire aliases: maxAmountRequired,
                            #   mimeType, payToAddress)
    PaymentRequirement,     # 402 body; dump(by_alias=True, exclude_none=True)
    PaymentResponse,        # settlement receipt (spec §5.3 + contract §5.2)
    PaymentReceipt,         # alias of PaymentResponse (compat only)
    Scheme,                 # StrEnum: exact | upto
    SupportedAsset,         # supported_assets[] table row
    ToolCatalog, ToolCatalogEntry, ToolPricing,  # pricing/catalog (kept)
    VerifyResult,           # facilitator verify outcome (contract §5.1)
    X402_VERSION, X402Tier,
)
```

- Construct with snake_case **or** camelCase aliases; serialize with
  `model_dump(by_alias=True, exclude_none=True)`.
- `maxAmountRequired` is a strict string of atomic units (no int/float
  coercion — the pricing layer must `str(asset_to_atomic(...))`).
- `network` accepts the 8 known legacy names and normalises to CAIP-2;
  unknown/non-CAIP-2 values raise `ValueError`.
- `ToolPricing.to_payment_option(...)` is the sanctioned bridge from catalog
  pricing to an `accepts[]` entry.

## [REVIEW] flags

1. **Protocol version.** The models emit `x402Version: 1` with the
   `maxAmountRequired`/`payToAddress` wire keys because that is what the
   fleet contract (§2.2) and `x402-py::build_payment_required` emit. The
   official v2 spec (`x402-specification-v2.md` §5.1) instead mandates
   `x402Version: 2`, `amount` (not `maxAmountRequired`), `payTo` (not
   `payToAddress`) and a top-level `ResourceInfo` object. The orchestrator
   must decide whether the fleet migrates to v2 wire shape; the models are
   structured so that migration is additive (a `PaymentRequirementV2`), not a
   rewrite. Note `app/routers/x402_verifier.py` already imports the official
   SDK v2 types (`PaymentPayload`/`PaymentRequirements`), which are v2-shaped
   — the two halves of this repo currently disagree.
2. **`payToAddress` vs `payTo`.** Official spec (both versions) names the
   recipient field `payTo`; `x402-py::build_payment_required` and the fleet
   contract use `payToAddress`. Followed the fleet contract (task mandate:
   match `build_payment_required` exactly). Confirm with the gateway agent.
3. **`networks`/`supported_assets` are a fleet extension**, not official spec
   fields (additive; spec clients ignore unknown keys). Their row shapes
   mirror `x402-py` tables exactly.
4. **`settled_at` typed as ISO-8601 string** (contract §5.2 shows no type).
   If the middleware agent prefers `datetime`, switch before wiring receipts.
5. **Router count.** Task brief said 26 `app/routers/x402_*.py`; actual count
   is 27 (verified with `find`).

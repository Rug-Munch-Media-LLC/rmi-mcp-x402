# rmi-mcp-x402 — Architecture

**Status:** canonical (spec-compliant target; see conformance review)
**Last updated:** 2026-08-21

## Position in the fleet

**Consolidation decision:** `rmi-mcp-x402` is the **canonical** x402 payment
surface. `x402-gateway` is archived; x402 capability duplicated inside
`rmi-backend` routers is being migrated here. The domain layer **vendors and
delegates to `x402-py`** instead of reimplementing the protocol (contract G10).

## Data flow (HTTP transport, `authorization` flow)

```
Client                      Server (this repo)                Facilitator (external / self-hosted)
  │ GET /paid                             │                              │
  ├──────────────────────────────────────▶│                              │
  │         402 PAYMENT-REQUIRED(accepts[])│                             │
  │◀──────────────────────────────────────┤                              │
  │ pays on-chain → verified id           │                              │
  │ GET /paid + X-Payment-ID: <id>       │                              │
  ├──────────────────────────────────────▶├── verify(payload/reqs)─────▶│
  │                                       │◀──── isValid / verified ────┤
  │                                       │  executes the paid tool      │
  │                                       ├── settle(payload/reqs)─────▶│
  │         200 (resource) + PAYMENT-RESPONSE                            │
  │◀──────────────────────────────────────┤◀──── success + tx_hash ──────┤
```

Verification is read-only (`/verify`); settlement (`/settle`) commits funds.
Replay: verified ids are caller-bound, TTL'd, and rejected if replayed by a
different caller. Pricing is server-authoritative (atomic-unit comparison —
`resolve_price_usd` + `validate_client_amount`).

## Domain module map — `app/domain/x402/`

| Module | Role | Spec status |
|---|---|---|
| `models.py` | Pydantic models | ⚠️ **legacy** — exports `PaymentRequest{tool,chain,user_address}`, `PaymentReceipt`, `ToolPricing`; has **no** `PaymentRequirement`/`PaymentOption` | 
| `middleware.py` | 402 enforcement, pricing, on-chain verify | ⚠️ **legacy** — `X-Payment: base64({tx_hash,signature})`, HMAC invoice, bespoke USDC Transfer-log scanner (G2/G3/G4) |
| `router.py` | `GET /api/v1/x402/{catalog,usage,receipts/{tx_hash}}`, `POST /verify` | ⚠️ **legacy** |
| `service.py` | `X402Service` facade (catalog/facilitators) | ⚠️ legacy facade → delegates to legacy routers |
| `tools_router.py` | paid tool endpoints (`/api/v1/x402-tools/*`) | gated by legacy middleware |
| `tools/` | tool handlers (token/wallet/news/scanner/market/label/report) | app logic |

Spec-compliant target modules (to land at reconciliation):

| Module | Intended |
|---|---|
| `models` | `PaymentRequired{x402Version,error,resource,accepts[],extensions}` + per-accept `PaymentRequirement`/`PaymentOption` (`scheme,network,amount/payTo/maxTimeoutSeconds,extra`) |
| `middleware` | 402 + `PAYMENT-REQUIRED` header; replay via verified id + `X-Payment-ID` |
| `service` | `FacilitatorRouter.verify/settle(...)` + `register_facilitator(network, impl)`; self-host EIP-3009/EIP-7702 + SPL |
| `router` | health exposing `VERIFICATION_STATUS` (`evm/solana=live`, `tron=scaffolded`) |

## Vendor-SDK verifier (live)

`app/routers/x402_verifier.py` is the **single source of truth** for
verification and uses the official SDK (`x402.http`, `x402.server`,
`x402.mechanisms.evm.exact`):

- `VerifierClient.verify_payment(payload, network=None) → {verified, reason, tx_hash, payer, amount, network}`
- `VerifierClient.settle_payment(payload, network=None) → {settled, tx_hash, network, error?}`
- Facilitator (PayAI) via `HTTPFacilitatorClient`; `x402ResourceServer`
  registers `exact` on `eip155:*`. **Fail-fast**: SDK missing ⇒ startup error,
  no silent fallback.

## Current issues (honest)

- `app/domain/x402/{models,middleware,router,service}.py` remain the **legacy
  T34/non-spec** implementation (contract G1–G9 open).
- `app/routers/` carries many legacy `x402_*` routers worth ~700 KB of surface;
  `app/routers/x402_enforcement.py` is the largest file (~103 KB). They are
  candidates for removal once the domain + verifier fully own the path.
- No CI was previously a real gap; CI now exists (Forgejo + GitHub workflows)
  and is verified. See [README](README.md).

## Deployment

Not yet deployed as a standalone container.

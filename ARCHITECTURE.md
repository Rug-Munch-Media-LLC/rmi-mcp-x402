# Coffer — Architecture

**Status:** canonical (spec-compliant; conformance reviewed)
**Last updated:** 2026-08-21

Coffer is the self-hosted x402 payment facilitator: an HTTP treasury, sovereign
and open. This document describes the modular layering, the 402 data flow, the
facilitator-router verify/settle contract, and the honest build status.

## Layering — what lives where

```
┌──────────────────────────────────────────────────────────────────────┐
│  coffer/  — public facade                                          │
│  `import coffer; @coffer.require_payment(asset=, network=, amount=)` │
│  thin, ergonomic re-export of the domain surface                    │
├──────────────────────────────────────────────────────────────────────┤
│  app/domain/x402/extensions/  — optional protocol extensions        │
│  payment-identifier (idempotency) · gas-sponsoring (EIP-2612 /       │
│  ERC-20 approval) · schemes (upto, batch-settlement: scaffolded)     │
├──────────────────────────────────────────────────────────────────────┤
│  app/domain/x402/  — the core domain                                │
│  models.py · middleware.py (require_payment) · service.py            │
│  (FacilitatorRouter.verify/settle, SelfFacilitator, pricing)         │
├──────────────────────────────────────────────────────────────────────┤
│  vendor/x402/  — vendored x402 reference SDK                         │
│  scheme.py · facilitator.py · self_facilitator.py · solana_verifier  │
│  handler.py · middleware.py · batch.py                               │
├──────────────────────────────────────────────────────────────────────┤
│  app/mcp/  — MCP manifest + x402 MCP server                          │
│  manifest.py · x402_mcp_server.py · server.py                        │
└──────────────────────────────────────────────────────────────────────┘
```

Dependency direction is strict and acyclic: **facade → domain → vendored spec**.
The domain **delegates to `x402-py`** (vendored) for protocol logic rather than
reimplementing it; extensions layer optional behavior on top of the core exact
flow; the facade is a thin, import-friendly surface. No layer reaches through to
another.

### 2026 design standards

| Standard | How Coffer meets it |
| --- | --- |
| **Modular** | strict layering above; swap a layer, keep the API |
| **Typed** | Pydantic v2 wire models, strict mypy on the domain |
| **Async-first** | `verify` / `settle` are `async` throughout |
| **Self-hostable** | runs in your process; no hosted facilitator required |
| **No external facilitator required** | `SelfFacilitator` settles on-chain with your keys |

## The 402 data flow (HTTP transport)

```
Client                          Coffer (your server)               Facilitator (self / hosted)
  │ GET /paid                            │                               │
  ├──────────────────────────────────────▶│                               │
  │          402 PAYMENT-REQUIRED(accepts[])                              │
  │◀──────────────────────────────────────┤                               │
  │ pays on-chain → verified id           │                               │
  │ GET /paid + X-Payment-ID: <id>       │                               │
  ├──────────────────────────────────────▶├── verify(payment/reqs) ─────▶│
  │                                       │◀──── isValid / verified ─────┤
  │                                       │  executes the paid tool       │
  │                                       ├── settle(payment/reqs) ─────▶│
  │         200 (resource) + PAYMENT-RESPONSE                             │
  │◀──────────────────────────────────────┤◀──── success + tx_hash ───────┤
```

Verification is **read-only** (`/verify`); settlement (**`/settle`**) commits
funds. Replay protection: verified ids are caller-bound, TTL'd, and rejected when
replayed by a different caller. Pricing is server-authoritative (atomic-unit
comparison via `resolve_price_usd` + `validate_client_amount`).

## FacilitatorRouter — verify / settle

`FacilitatorRouter` (`app/domain/x402/service.py`) is the settlement brain:

- **`register_facilitator(network, impl)`** — register any facilitator for a
  network; `impl` must expose `async verify(payment) -> VerifyResult` and
  `async settle(payment) -> str | None`. This is how **TRON** is scaffolded.
- **`SelfFacilitator`** (`vendor/x402/self_facilitator.py`) — the in-process,
  self-hosted facilitator. EIP-3009 / EIP-7702 on EVM, SPL on Solana. Enabled by
  `X402_SELF_FACILITATE=1` (default on). Your keys, your treasury.
- **Routing** — health-aware with automatic fallback; hosted facilitators
  (Coinbase, PayAI, Cloudflare) are optional peers, not requirements.

## Honest build status

| Network | Verification | Status |
| --- | --- | --- |
| EVM (Base, Ethereum, Arbitrum, Optimism, Polygon) | EIP-3009 / EIP-7702 on-chain | ✅ **live** (`VERIFICATION_STATUS.evm`) |
| Solana | SPL `transferChecked` RPC | ✅ **live** (`VERIFICATION_STATUS.solana`) |
| TRON | — | 🧱 **scaffolded** · structure/price/asset validated; returns `verified:false: "needs_facilitator"` until an impl is registered |

Exposed honestly on `/v1/x402/health` (`evm=live`, `solana=live`,
`tron=scaffolded:needs_facilitator`). We **never** claim a scaffolded network is
verified.

## Configuration env

| Var | Meaning |
| --- | --- |
| `X402_SELF_FACILITATE` | `1`/`true` → self-hosted settlement (default on) |
| `X402_EIP7702_RPC_BASE\|1\|42161` | JSON-RPC endpoints for EIP-7702 self-verify |
| `X402_WALLET_<NETWORK>` | per-network receiving address (drives `accepts[]`) |

## Deployment

Not yet shipped as a standalone container; runs in-process as a FastAPI route
guard or an MCP tool gate. Docker packaging is a roadmap item.

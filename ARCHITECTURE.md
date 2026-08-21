# Coffer — Architecture

**Status:** canonical (spec-compliant; conformance reviewed · honesty fixed)
**Last updated:** 2026-08-21

Coffer is the self-hosted x402 payment facilitator: an HTTP treasury, sovereign
and open. This document describes the modular layering, the 402 data flow, the
facilitator-router verify/settle contract, and the honest build status (live vs
scaffolded — we never report a scaffolded path as verified).

## Layering — what lives where

```
┌──────────────────────────────────────────────────────────────────────────┐
│  coffer/  — public facade                                               │
│  `import coffer; @coffer.require_payment(asset=, network=, amount=)`     │
│  thin, ergonomic re-export of the domain (coffer.mcp = MCP manifest)     │
├──────────────────────────────────────────────────────────────────────────┤
│  app/domain/x402/extensions/  — optional 2026 protocol extensions       │
│  payment-identifier (idempotency) · gas-sponsoring (EIP-2612 / ERC-20    │
│  approval, H1/M1) · schemes (upto, batch-settlement: scaffolded)         │
├──────────────────────────────────────────────────────────────────────────┤
│  app/domain/x402/  — the core domain (service.py decomposed)             │
│  models.py · middleware.py (require_payment) · router.py (/verify,/settle,│
│  /health) · solana_verifier.py · EVM verify (EIP-3009/permit2/7702)      │
│                                                                          │
│  service.py = six cohesive concerns (see below):                         │
│   • replay    — ReplayStore (atomic nonce, H2)                           │
│   • pricing   — resolve_price_usd / validate_client_amount (atomic)      │
│   • facilitator_config — FacilitatorConfig + env parsing                 │
│   • self_facilitator  — SelfFacilitator (your keys, EIP-3009/7702)       │
│   • evm_verify — EIP-7702 receipt verify (read-only, C1) + ecrecover     │
│   • router    — FacilitatorRouter (register/routing/verify/settle)       │
├──────────────────────────────────────────────────────────────────────────┤
│  vendor/x402/  — vendored x402 reference SDK (2026, v1 + v2)             │
│  scheme.py · facilitator.py · self_facilitator.py · solana_verifier      │
│  handler.py · middleware.py · batch.py                                   │
└──────────────────────────────────────────────────────────────────────────┘
```

Dependency direction is strict and acyclic: **facade → domain → vendored spec**.
The domain **delegates protocol logic to `x402-py`** (vendored) rather than
reimplementing it; extensions layer optional behavior on top of the core exact
flow; the facade is a thin import-friendly surface. No layer reaches through to
another.

### 2026 design standards

| Standard | How Coffer meets it |
| --- | --- |
| **Modular** | strict layering above; `service.py` split into six cohesive concerns (replay / pricing / facilitator_config / self_facilitator / evm_verify / router) |
| **Typed** | Pydantic v2 wire models, strict mypy on the domain |
| **Async-first** | `verify` / `settle` are `async` throughout; atomic nonce under `asyncio.Lock` |
| **Self-hostable** | runs in your process; no hosted facilitator required |
| **Honest status** | live vs scaffolded surfaced on `/v1/x402/health`; verify-only never reported as settled |

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
replayed by a different caller (atomic nonce burn, `H2`). Pricing is
server-authoritative (atomic-unit comparison via `resolve_price_usd` +
`validate_client_amount`).

## FacilitatorRouter — verify / settle

`FacilitatorRouter` (`app/domain/x402/service.py`, router concern) is the
settlement brain:

- **`register_facilitator(network, impl)`** — register any facilitator for a
  network; `impl` must expose `async verify(payment) -> VerifyResult` and
  `async settle(payment) -> str | None`. This is how **TRON** is scaffolded.
- **`SelfFacilitator`** (self_facilitator concern) — the in-process, self-hosted
  facilitator. EIP-3009 / EIP-7702 on EVM, SPL on Solana. Enabled by
  `X402_SELF_FACILITATE=1` (default on). Your keys, your treasury.
- **Routing** — health-aware with automatic fallback; hosted facilitators
  (Coinbase, PayAI, Cloudflare) are optional peers, not requirements.

## Honest build status

| Network | Verification | Status |
| --- | --- | --- |
| EVM (Base, Ethereum, Arbitrum, Optimism, Polygon) | EIP-3009 / EIP-7702 on-chain | ✅ **live** (`VERIFICATION_STATUS.evm`) — EIP-7702 verifies receipts; settlement only when a settle key is configured (C1 fail-closed) |
| Solana | SPL `transferChecked` RPC | ✅ **live** (`VERIFICATION_STATUS.solana`) — RPC verifies receipts; settlement only via a configured settle path (C2 fail-closed) |
| TRON | — | 🧱 **scaffolded** · structure/price/asset validated; returns `verified:false: "needs_facilitator"` until an impl is registered |

Gas-sponsoring (`eip2612` / `erc20-approval`) is **scaffolded without a
`sponsor_impl`**: signature/structure verified, `tx_hash` **never fabricated**
(H1), and settlement raises `not implemented` until a sponsor key is configured.
The `upto` and `batch-settlement` schemes are scaffolded — documented, not
reported verified.

All of this is exposed honestly on `/v1/x402/health` (`evm=live`,
`solana=live`, `tron=scaffolded:needs_facilitator`). We **never** claim a
scaffolded network, scheme, or gas-sponsoring flow is verified.

## Configuration env

| Var | Meaning |
| --- | --- |
| `X402_SELF_FACILITATE` | `1`/`true` → self-hosted settlement (default on) |
| `X402_EIP7702_RPC_BASE\|1\|42161` | JSON-RPC endpoints for EIP-7702 self-verify |
| `X402_WALLET_<NETWORK>` | per-network receiving address (drives `accepts[]`) |
| `X402_NONCE_TTL` | replay-nonce TTL (default 900s) |

## Deployment

Not yet shipped as a standalone container; runs in-process as a FastAPI route
guard or an MCP tool gate. Docker packaging is a roadmap item.

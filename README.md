# rmi-mcp-x402 — Canonical x402 Payment Surface

Spec-compliant **x402** (HTTP `402 Payment Required`) payment layer for Rug Munch
Intelligence. Implements the official **x402 protocol v1 + v2** (`exact` scheme)
as the canonical x402/MCP payment surface for the fleet. This repo **vendors
`x402-py`** and delegates verification/settlement to its public API rather than
reimplementing the protocol from scratch.

> Canonical conformance contract: [`docs/X402-SPEC-CONTRACT.md`](docs/X402-SPEC-CONTRACT.md)
> (G1–G10 gap analysis) and the authoritative spec under
> [`docs/x402-official-spec/`](docs/x402-official-spec/).

## The 402 flow

```
client ──GET /paid (no payment)────────▶ server
server ◀─402 + PAYMENT-REQUIRED (base64 PaymentRequired) + accepts[]── client
client pays a facilitator (or self-settles on-chain), gets a VERIFIED id
client ──GET /paid + X-Payment-ID: <verified id>──▶ server
server ──200 (resource)──▶ client ✓   (facilitator /settle records settlement)
```

No API keys, no accounts. The 402 body advertises the accepted
(`network`,`asset`) pairs; the client picks one, pays, and replays with the
verified payment id (caller-bound, TTL'd — replayed by another caller is
rejected).

## Networks (CAIP-2) and schemes

- **EVM — live:** `eip155:8453` (base), `eip155:1` (ethereum),
  `eip155:42161` (arbitrum), `eip155:10` (optimism), `eip155:137` (polygon).
- **Solana — live:** `solana:mainnet` (SPL USDC `transferChecked` verifier).
- **TRON — scaffolded:** `tron:mainnet` — structure/price/asset validated only,
  returns `verified:false reason:"needs_facilitator"`. **Not** reported live.
- **Scheme:** `exact` (EIP-3009, EIP-7702, SPL). `upto`/`batch-settlement`
  are future. Verification methods: `eip3009`, `permit2`, `native`, `spl`,
  `trc20`.

## Facilitator routing

`FacilitatorRouter` picks the best enabled facilitator with health-aware
fallback:

| Facilitator | URL | Default |
|---|---|---|
| Coinbase | `https://x402.org/facilitator` | on |
| PayAI | `https://facilitator.payai.network` | on |
| Cloudflare | env URLs | off |
| Self-hosted | `X402_SELF_FACILITATE=1` — EIP-3009/EIP-7702 (EVM) + SPL (Solana) | off |

## Spec conformance level

- **Protocol:** v1 + v2 (`PaymentRequired`/`PaymentPayload`/`SettlementResponse`).
- **Schemes:** `exact` (EVM+SVM). **Networks:** EVM + Solana **live**, TRON **scaffolded**.
- **Verification status** (exposed via `/v1/x402/health`): `evm=live`,
  `solana=live`, `tron=scaffolded:needs_facilitator`.
- **Pricing:** server-authoritative (`resolve_price_usd` +
  `validate_client_amount` in atomic units; underpayment refused, overpayment ok).

> ⚠️ Coverage reality: the vendor-SDK verifier
> (`app/routers/x402_verifier.py`) is live; `app/domain/x402/models.py` and
> `app/domain/x402/middleware.py` still carry the **legacy T34/non-spec**
> interfaces (G1–G10 open). See `docs/X402-CONFORMANCE-REVIEW.md` for the
> merge-reconciliation items.

## Scope

- x402 payment verification + settlement (facilitator & self-hosted)
- MCP server exposing RMI security tools (paid)
- Verified-badge program domain logic

## Fleet dependencies

- RMI backend for security data; Qdrant, Redis, Postgres via rmi-backend.
- Vendored [`x402-py`](https://docs.x402.org) (sibling repo) for all protocol
  logic — fail-fast, no silent fallback.

## Standards

This repo follows https://git.rugmunch.io/RugMunchMedia/standards

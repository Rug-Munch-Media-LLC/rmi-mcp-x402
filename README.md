# Coffer — The Self-Hosted x402 Payment Facilitator

> **Your HTTP treasury, sovereign and open.** Accept `402 Payment Required`
> micropayments in one line of Python — on your own infrastructure, with zero
> vendor lock-in. No Coinbase, no closed facilitator, no hostage dollars.

[![x402 v1+v2](https://img.shields.io/badge/x402-v1%20%2B%20v2-blue)](#2026-spec-compliance)
[![license MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![self-hosted](https://img.shields.io/badge/deploy-self--hosted-purple)](#why-coffer)
[![spec-compliant](https://img.shields.io/badge/conformance-canonical-brightgreen)](docs/x402-official-spec/)


```python
import coffer

@coffer.require_payment(asset="USDC", network="base", amount=0.001)
async def paid_endpoint():
    return {"data": "you paid for this"}
```

That's it. That decorator is a spec-exact `HTTP 402` → verify → settle fastAPI
gate. And because **Coffer ships its own on-chain facilitator**, the whole rail —
offer, verification, settlement, replay protection — runs inside your process.
You keep the keys, you keep the treasury, you keep the custodian.

---

## Why Coffer

**Sovereign payments.** Coffer is the OSS x402 *self*-facilitator. Every
settlement path defaults to **your own signature** (EIP-3009 / EIP-7702 on EVM,
SPL `transferChecked` on Solana). No third-party facilitator is required to
collect revenue — `X402_SELF_FACILITATE=1` flips you to pure on-chain
self-settlement. The treasury is *your* wallet, not a hosted ledger.

**Zero vendor lock-in.** Wire format is the open x402 standard; the facilitator
is swappable behind `FacilitatorRouter`; the facade is a tiny public API. If a
better facilitator exists tomorrow, you point `register_facilitator()` at it and
move on. Your payments are never held hostage to a service's uptime, pricing,
migration, or shutdown.

**The most payment options.** Today Coffer advertises **[6 live networks × 24
live (network, asset) payment options](#payment-options)** — the broadest
self-hostable set in the facilitator landscape — and it costs zero to extend.
EVM: USDC, USDT, ETH, WETH (+ ARB on Arbitrum, POL on Polygon) across Base,
Ethereum, Arbitrum, Optimism and Polygon. SVM: USDC, USDT, SOL on Solana. TRON
is scaffolded and waiting for a facilitator. Agents see every payable pair in a
single `accepts[]` and pick freely.

**AI-forward.** Agents don't open wallets in browsers — they pay over HTTP.
Coffer is **MCP-native**: vendor `FastMCP`, gate your agent tools with the same
decorator, and expose a Bazaar-discoverable paid MCP server. Any agent that
speaks the x402 handshake can pay your API and get the resource back. Payments
for the machine economy, done by machines.

---

## Payment options

Canonical `(network, asset)` matrix, **CAIP-2** network ids, `exact` scheme with
atomic-unit amounts. Advertised additively via `build_multi_asset_accepts`, so a
client reads **every** payable pair and chooses.

### EVM — live (`eip155:*`, EIP-3009 / EIP-7702 / Permit2)

| Network (CAIP-2) | Chain | USDC | USDT | ETH | WETH | ARB | POL |
| ---------------- | ----- | :--: | :--: | :-: | :--: | :-: | :-: |
| `eip155:8453` | Base | ✅ | ✅ | ✅ | ✅ | — | — |
| `eip155:1` | Ethereum | ✅ | ✅ | ✅ | ✅ | — | — |
| `eip155:42161` | Arbitrum | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| `eip155:10` | Optimism | ✅ | ✅ | ✅ | ✅ | — | — |
| `eip155:137` | Polygon | ✅ | ✅ | ✅ | ✅ | — | ✅ |

### SVM — live (`solana:mainnet`, SPL `transferChecked`)

| Network (CAIP-2) | USDC | USDT | SOL |
| ---------------- | :--: | :--: | :-: |
| `solana:mainnet` | ✅ | ✅ | ✅ |

### TRON — scaffolded (`tron:mainnet`)

| Network (CAIP-2) | USDT | TRX | Status |
| ---------------- | :--: | :-: | ------ |
| `tron:mainnet` | ✅ | ✅ | `verified:false` — structure/price/asset validated; needs a facilitator impl |

**[6 live networks, 24 live (network, asset) options.]** Honest status is
exposed on `/v1/x402/health`: `evm=live`, `solana=live`,
`tron=scaffolded:needs_facilitator`.

> [REVIEW] DAI and MATIC are priced in the oracle but **not yet** in the
> advertised `accepts[]` matrix. We document only the live/payable set above.

---

## Extensions — 2026 extension spec

| Extension | Status |
| --------- | ------ |
| **payment-identifier** — idempotent, caller-bound, TTL'd verified ids (replay by a different caller rejected) | ✅ implemented |
| **EIP-2612 gas-sponsoring** — gasless permit-style payments | ✅ implemented |
| **ERC-20 approval gas-sponsoring** — gasless ERC-20 approve payments | ✅ implemented |
| **`upto`** scheme — authorize up to a maximum, settlement flexible | 🔜 future (scheme landed in official SDK) |
| **batch-settlement** — deposit + escrow-style multi-settle (before/after-handler phases) | 🔜 future |

---

## Comparison — Coffer vs the facilitator field

Source: [`docs/x402-official-spec/dev-tools__facilitators.md`](docs/x402-official-spec/dev-tools__facilitators.md)
(the official facilitator catalog). We win on the axis nobody else covers:
**self-hosting + open source + breadth at once.**

| Capability | **Coffer** | Coinbase (CDP) | PayAI | Cloudflare | Fireblocks | Mogami |
| ---------- | :---: | :---: | :---: | :---: | :---: | :---: |
| Open source (OSS) | ✅ MIT | ❌ | ❌ | ❌ | ✅ | ✅ |
| **Self-hosted** (no hosted dependency) | ✅ | ❌ | ❌ | ❌ | ⚠️ Fireblocks vault | ⚠️ Docker, Base-only |
| **Networks live** | **6** (5× EVM + Solana) | Solana mostly | multi | — | multi-chain | Base only |
| **Live payment options** | **24** | USDC-centric | all tokens | — | — | USDC |
| Self-settle in-process (no facilitator) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| TRON | scaffolded | ❌ | — | ❌ | ❌ | ❌ |
| MCP payment wrapper | ✅ native | ❌ | ❌ | ❌ | ❌ | ❌ |

**Verdict:** on **self-hosting** we beat every hosted facilitator (Coinbase /
PayAI / Cloudflare route settlement through their service by design). On
**network breadth** we beat every self-hostable OSS rival (Mogami is Base-only;
Fireblocks is open-source but couples settlement to its hosted vault). Coffer is
the only option that is **OSS, self-hosted, self-settling, and the widest payment
surface**.

---

## Quickstart — one line

```bash
pip install coffer
```

```python
import coffer
from fastapi import FastAPI

app = FastAPI()

@app.get("/paid")
@coffer.require_payment(asset="USDC", network="base", amount=0.001)
async def paid_endpoint():
    return {"data": "paid in one line"}
```

Curl it unpaid → `402 Payment Required` with the full `PAYMENT-REQUIRED`
header (`accepts[]` built from your configured networks/assets). Pay via any
configured facilitator **or the built-in self-facilitator**, replay with
`X-Payment-ID`, get `200`.

Full runnable examples:
- [`examples/fastapi_paid_endpoint.py`](examples/fastapi_paid_endpoint.py) — minimal `@require_payment` route
- [`examples/mcp_server_paid.py`](examples/mcp_server_paid.py) — MCP server with a paid tool (client auto-pay bridge + server-side gate)
- [`examples/self_hosted_facilitator.py`](examples/self_hosted_facilitator.py) — stand up your own verify/settle, no Coinbase

---

## Facilitator routing

`FacilitatorRouter` picks a health-aware facilitator with automatic fallback —
**including your own in-process self-facilitator**.

| Mode | Setting | Verifies / settles |
| --- | --- | --- |
| **Self-hosted (default)** | `X402_SELF_FACILITATE=1` | your EIP-3009 / EIP-7702 (EVM) + SPL (Solana) |
| Coinbase / x402.org | `https://x402.org/facilitator` | hosted (optional) |
| PayAI | `https://facilitator.payai.network` | hosted (optional) |
| Cloudflare | env URLs | hosted (opt-in) |

Drop the hosted facilitators entirely and Coffer settles on-chain with **no
external dependency**.

---

## 2026 spec compliance

- **Protocol:** v1 **and** v2 wire formats (`PaymentRequired` / `PaymentPayload`
  / `SettlementResponse`). Exact header scheme: `HTTP 402` +
  `PAYMENT-REQUIRED: <base64 JSON>` + `X-Payment-Required: true` +
  `X-Payment-Options: x402`; replay via `X-Payment-ID`.
- **Schemes:** `exact` (EVM `eip3009` / `permit2` / `native` + SVM `spl`),
  `upto` / `batch-settlement` teed up for future.
- **Extensions:** keys `payment-identifier`, `eip2612-gas-sponsoring`,
  `erc20-approval-gas-sponsoring`.
- **Vendored x402 spec:** the repo carries the official `docs/x402-official-spec/`
  and delegates verification/settlement through `x402-py`'s public API **or**
  the self-hosted verifier — fail-fast, no silent fallback.

> See [ARCHITECTURE.md](ARCHITECTURE.md) for the 402 flow and module layering,
> [CONTRIBUTING.md](CONTRIBUTING.md) to add a network or extension.

---

## Scope

- x402 payment verification + settlement (facilitator & self-hosted)
- MCP server exposing a paid, Bazaar-discoverable tool surface
- 2026 extension support (idempotency, gas-sponsoring, schemes)

## Standards

Follows https://git.rugmunch.io/RugMunchMedia/standards · MIT License

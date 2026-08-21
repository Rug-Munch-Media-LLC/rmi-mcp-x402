# RMI MCP x402 — The Self-Hostable OSS x402 Payment Layer

[![x402 spec](https://img.shields.io/badge/x402-v1%20%2B%20v2-blue)](#2026-spec-compliance)

**Accept x402 402-payments in one line of Python. Self-hostable — no Coinbase,
no vendor lock-in. MCP-native by default.**

```
@require_payment()
async def paid_endpoint(request: Request):
    return {"data": "you paid!"}
```

`rmi-mcp-x402` is a spec-compliant **x402** (`HTTP 402 Payment Required`) payment
surface that is *both* a server (gate any FastAPI route / MCP tool behind a
micropayment) *and* a facilitator mesh (route settlement across Coinbase, PayAI,
Cloudflare **or your own on-chain self-hosted facilitator**). Because every piece
ships as MIT-licensed OSS, you can run the **entire** payment rail in your own
infrastructure — no third-party dependency required to collect revenue.

- **One-line integration** — `@require_payment()` on any FastAPI handler returns
  a spec-exact `402 + PAYMENT-REQUIRED` (base64 `PaymentRequired`, `accepts[]`).
- **Self-hostable** — `X402_SELF_FACILITATE=1` lets you settle on-chain
  yourself (EIP-3009 / EIP-7702 on EVM, SPL on Solana). The only OSS x402
  facilitator that needs **zero** hosted services to operate.
- **MCP-native** — vendor `FastMCP`; gate your AI-agent tools with the same
  decorator, expose a Bazaar-discoverable paid MCP server. See the
  [`MCP-server example`](examples/mcp_server_paid.py).
- **The most networks & assets** in the facilitator field — 6 live networks,
  23 live (network × asset) payment options today.

## Payment options

Full list of accepted (`network`, `asset`) pairs, in **CAIP-2** ids with the
exact-amount scheme (EIP-3009 / EIP-7702 on EVM, `transferChecked` on Solana).

### EVM — live (`eip155:*`, via EIP-3009 / EIP-7702)

| Network (CAIP-2)     | Chain      | Accepted assets                      |
| -------------------- | ---------- | ------------------------------------ |
| `eip155:8453`        | Base       | USDC, USDT, ETH, WETH, DAI           |
| `eip155:1`           | Ethereum   | USDC, USDT, ETH, WETH                |
| `eip155:42161`       | Arbitrum   | USDC, USDT, ARB, WETH                |
| `eip155:10`          | Optimism   | USDC, USDT, WETH                     |
| `eip155:137`         | Polygon    | USDC, USDT, POL, MATIC               |

### SVM — live (`solana:mainnet`, SPL `transferChecked`)

| Network (CAIP-2)  | Accepted assets    |
| ----------------- | ------------------ |
| `solana:mainnet`  | USDC, USDT, SOL    |

### TRON — scaffolded (`tron:mainnet`)

| Network (CAIP-2)  | Accepted assets     | Status                   |
| ----------------- | ------------------- | ------------------------ |
| `tron:mainnet`    | USDC, USDT, TRX     | `verified:false` — needs facilitator (structure/price/asset validated) |

**Count: 6 live networks × 23 live (network, asset) options (+ 3 TRON
scaffolded).** Advertised accept entries are additive (`build_multi_asset_accepts`),
so AI agents see every payable pair and pick freely.

## Extensions

Beyond the core `exact` scheme, the surface supports the 2026 extension spec:

| Extension | Status |
| --------- | ------ |
| **payment-identifier** — idempotent, caller-bound, TTL'd verified ids (replay by a different caller rejected) | ✅ implemented |
| **EIP-2612 / ERC-20 approval gas-sponsoring** — gasless permit-style payments | ✅ scaffolded to spec |
| **`upto`** scheme — authorize up to a maximum, settlement flexible | 🔜 future (scheme landed in official SDK) |
| **batch-settlement** — deposit + escrow-style multi-settle (before/after-handler phases) | 🔜 future |

## Comparison — why RMI MCP x402

We are the **only self-hostable open-source** option in the facilitator
landscape **and** the one with the broadest network/asset matrix among
self-hostable projects:

| Capability | **RMI MCP x402** | Coinbase (CDP) | PayAI | Cloudflare | Fireblocks | Mogami |
| ---------- | :---: | :---: | :---: | :---: | :---: | :---: |
| Open source (OSS) | ✅ MIT | ❌ | ❌ | ❌ | ✅ | ✅ |
| **Self-hostable** (no hosted dependency) | ✅ | ❌ | ❌ | ❌ | ⚠️ needs Fireblocks vault | ⚠️ Docker, Base-only |
| **Networks live** | **6 (5× EVM + Solana)** | Solana mostly | multi | — | multi-chain | Base only |
| Assets per network | breadth (9 live assets) | USDC-centric | all tokens | — | — | USDC |
| TRON | scaffolded | ❌ | — | ❌ | ❌ | ❌ |
| MCP payment wrapper | ✅ native | ❌ | ❌ | ❌ | ❌ | ❌ |
| KYT/OFAC checks | opt-in | ✅ | ❌ | — | — | ❌ |

**On network breadth we beat every self-hostable OSS rival** (Mogami is Base-only;
Fireblocks is open-source but couples settlement to its hosted vault). **On
self-hosting we beat every hosted facilitator** (Coinbase/PayAI/Cloudflare require
you to route through their service). See [`docs/x402-official-spec/dev-tools__facilitators.md`](docs/x402-official-spec/dev-tools__facilitators.md)
for the full competitor catalog; our live matrix above is the largest self-hostable
set in that list.

## Quickstart

```python
from fastapi import FastAPI, Request
from app.domain.x402.middleware import require_payment

app = FastAPI()

@app.get("/paid")
@require_payment()
async def paid_endpoint(request: Request):
    return {"data": "paid in one line"}
```

Run it, curl the endpoint unpaid → `402 Payment Required` with the full
`PAYMENT-REQUIRED` header. Pay via any configured facilitator (or self-hosted),
replay with `X-Payment-ID`, get `200`. Full examples:
[`examples/fastapi_paid_endpoint.py`](examples/fastapi_paid_endpoint.py) and
[`examples/mcp_server_paid.py`](examples/mcp_server_paid.py).

## Facilitator routing

`FacilitatorRouter` picks the best health-aware facilitator with automatic
fallback:

| Facilitator | URL | Default | Self-hosted alternative |
| --- | --- | --- | --- |
| Coinbase | `https://x402.org/facilitator` | on | — |
| PayAI | `https://facilitator.payai.network` | on | — |
| Cloudflare | env URLs | off | — |
| **Self-hosted** | `X402_SELF_FACILITATE=1` | off | EIP-3009/EIP-7702 (EVM) + SPL (Solana) |

You can **drop Coinbase entirely** — self-host mode settles on-chain with no
external dependency.

## 2026 spec compliance

- **Protocol:** v1 **and** v2 wire formats (`PaymentRequired` / `PaymentPayload` /
  `SettlementResponse`), exact header scheme: `HTTP 402` + `PAYMENT-REQUIRED:
  <base64 JSON>` + `X-Payment-Required: true` + `X-Payment-Options: x402`, replay
  via `X-Payment-ID`.
- **Schemes:** `exact` (EVM `eip3009`/`permit2`/`native` + SVM `spl`),
  `upto`/`batch-settlement` future. Extension keys: `payment-identifier`,
  `eip2612-gas-sponsoring`, `erc20-approval-gas-sponsoring`.
- **Health:** `/v1/x402/health` exposes `evm=live`, `solana=live`,
  `tron=scaffolded:needs_facilitator`.

> Spec reference: [`docs/x402-official-spec/`](docs/x402-official-spec). This repo
> vendors `x402-py` and delegates verification/settlement to its public API OR to
> your own self-hosted verifier — fail-fast, no silent fallback.

## Scope

- x402 payment verification + settlement (facilitator & self-hosted)
- MCP server exposing RMI security tools (paid, Bazaar-discoverable)
- Verified-badge program domain logic

## Fleet dependencies

- RMI backend for security data; Qdrant, Redis, Postgres via rmi-backend.
- Vendored [`x402-py`](https://docs.x402.org) (sibling repo) for all protocol logic.

## Standards

This repo follows https://git.rugmunch.io/RugMunchMedia/standards

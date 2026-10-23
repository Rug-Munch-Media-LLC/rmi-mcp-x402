# RMI MCP + x402 Gateway

> Model Context Protocol server exposing typed RMI crypto-security tools to AI agents, settled per call with x402 USDC payments.

**License:** [MIT](LICENSE) · **Canonical:** https://git.rugmunch.io/RugMunchMedia/rmi-mcp-x402
**Mirrors:** [GitHub](https://github.com/Rug-Munch-Media-LLC) · [GitLab](https://gitlab.com/rug-munch-media-group) · [Codeberg](https://codeberg.org/RugMunchMedia)

[![license MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/downloads/)
[![x402 v1+v2](https://img.shields.io/badge/x402-v1%20%2B%20v2-blue)](#x402-payment-rail)
[![MCP](https://img.shields.io/badge/MCP-typed%20tools-purple)](https://modelcontextprotocol.io/)

> **Status: Beta / active development.** Tool schemas and payment flow are
> stable; see [Honest status](#honest-status) for what is and isn't live.

## What It Is

This repo is the MCP-facing edge of the Rug Munch Intelligence platform:

- **MCP server** — typed tools over streamable HTTP / stdio, consumable from
  Claude Code, opencode, Cursor, or any MCP-capable agent.
- **x402 gateway** — gates premium tool calls behind per-request crypto
  micropayments, delegating verification and settlement to a self-hosted
  facilitator (see below).
- **Verified badge domain** — the badge logic for verified RMI listings.

It is **not** the scanning engine (that lives in `rmi-backend`) or the wallet
generator (that lives in `walletpress`).

## Tool Catalog

The gateway publishes a typed catalog (`app/mcp/server.py`). Core tools:

| Tool | Purpose |
|------|---------|
| `get_token_risk` | 0–100 token risk score with category breakdown |
| `get_wallet_analysis` | Wallet forensics: holdings, counterparties, labels |
| `get_deployer_reputation` | Deployer history and reputation signals |
| `get_news_sentiment` | News and social sentiment for an asset |
| `generate_report` | Structured investigation report |
| `query_catalog` | Search the tool/asset catalog |
| `find_similar_tokens` | Similarity lookup across indexed tokens |

The stdio surface (`mcp_stdio.py`) exposes the catalog over
`fastmcp run mcp_stdio.py:mcp`.

## x402 Payment Rail

The gateway **does not verify payments in-house**. It delegates verification
and settlement to a self-hosted [`x402-coffer`](https://git.rugmunch.io/RugMunchMedia/x402-coffer)
facilitator over HTTP. Coffer is the source of truth for whether a payment is
real; the gateway's `X-Payment-ID` gate **fails closed** when coffer cannot be
reached.

| Setting | Default | Meaning |
|---------|---------|---------|
| `X402_FACILITATOR_URL` | `http://localhost:8670` | Base URL of the coffer deployment |

Endpoints the gateway calls on coffer:

| Gateway call | Coffer endpoint | Purpose |
|--------------|-----------------|---------|
| payment re-check | `POST {X402_FACILITATOR_URL}/v1/x402/verify` | Confirm an `X-Payment-ID` is verified (caller-bound, TTL'd) |
| settlement | `POST {X402_FACILITATOR_URL}/v1/x402/settle` | Settle a verified payment |

**Fail-closed contract:** a payment-gated call is allowed through only when
coffer answers `verified: true`. If coffer is unreachable, times out, or
returns 5xx after bounded retries, the gateway answers **402 Payment Required**
— it never falls back to allowing the call through unverified. The gateway's
local payment store is caller/tx *memory* only; it cannot self-verify.

## Architecture

```
Agent (MCP client)
   │  tools/list, tools/call
   ▼
rmi-mcp-x402 gateway ──── app/mcp/         MCP server, tool catalog, tool manager
   │                 ├── app/domain/x402/  payment middleware, models, service
   │                 ├── app/domain/verified/  badge domain
   │                 └── app/api/v1/       HTTP API routes
   │  verify / settle (HTTP)
   ▼
x402-coffer (self-hosted facilitator)
   │
   ▼
RMI backend (security data) · Qdrant · Redis · Postgres
```

## Quickstart

```bash
# Install (editable)
make install

# Run the MCP server (streamable HTTP, :8001)
make dev

# Run the x402 facilitator locally (:8001)
make run

# Full stack: coffer + redis via Docker Compose
make up
```

Run `make help` for all targets. Quality gates: `make lint`, `make typecheck`,
`make test`, `make security`, and `make ci` (ruff + mypy + pytest).

## Configuration

Wallets resolve from the environment only — there are **no hardcoded wallet
defaults**. Unset resolves to `""` and the deployment **fails closed**:
`load_settings()` raises at boot and the app refuses to serve an unconfigured
402.

| Env var | Chain | Purpose |
|---------|-------|---------|
| `X402_WALLET` | EVM — default payee for all `eip155:*` | Receiving address |
| `X402_WALLET_SOLANA` | Solana (`solana:mainnet`) | Receiving address |
| `X402_WALLET_TRON` | TRON (`tron:mainnet`) | Receiving address |
| `X402_FACILITATOR_URL` | — | Coffer facilitator base URL |
| `REDIS_URL` | — | Payment nonce / replay store |

Per-network EVM overrides: `X402_WALLET_BASE` / `_ETH` / `_ARBITRUM` /
`_OPTIMISM` / `_POLYGON` (fall back to `X402_WALLET`). Old names are honored
as read-time aliases only.

## Honest Status

We do not overclaim. Scaffolded paths are never reported as verified:

- **TRON** is structural work only — shape, price, and asset are validated,
  and health reports `tron=scaffolded:needs_facilitator` until a real
  facilitator implementation is registered.
- **Verifying is not settling.** Self-verify paths read on-chain receipts only
  and fail closed with `needs_facilitator` rather than fabricating a settlement.
- **A bare HTTP-200 is not proof of payment** — an explicit verification or
  settlement signal is required before reporting success.

## Documentation

- [docs.cryptorugmunch.com/rmi-mcp-x402](https://docs.cryptorugmunch.com/rmi-mcp-x402) — product docs
- [`docs/X402-SPEC-CONTRACT.md`](docs/X402-SPEC-CONTRACT.md) — x402 conformance contract
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — module layering and the 402 flow
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — how to add a tool or a network

## Related Repositories

| Repo | Purpose |
|------|---------|
| [`x402-coffer`](https://git.rugmunch.io/RugMunchMedia/x402-coffer) | Self-hosted x402 payment facilitator |
| [`rmi-backend`](https://git.rugmunch.io/RugMunchMedia/rmi-backend) | Intelligence engine and data APIs |
| [`rmi-frontend`](https://git.rugmunch.io/RugMunchMedia/rmi-frontend) | Web dashboard |
| [`walletpress`](https://git.rugmunch.io/RugMunchMedia/walletpress) | Wallet management |

## License

MIT — see [LICENSE](LICENSE). Built by
[Rug Munch Media LLC](https://rugmunch.io).

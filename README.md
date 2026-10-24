# rmi-mcp-x402

An MCP server whose tools are paid per call over x402: an agent discovers a tool,
gets a 402 with a price, pays in USDC on-chain, and retries with proof of payment.
The facilitator is bundled, so it runs as one stack.

**Status:** beta · **Licence:** MIT, see [LICENSE](LICENSE).

Canonical: https://git.rugmunch.io/RugMunchMedia/rmi-mcp-x402

## Quickstart

Python 3.11+, Redis, and a receiving address.

```bash
git clone https://git.rugmunch.io/RugMunchMedia/rmi-mcp-x402.git
cd rmi-mcp-x402
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # X402_WALLET, X402_SELF_FACILITATE, REDIS_URL
python data_mcp_server.py
```

Compose runs the MCP server and its facilitator together:

```bash
docker compose up -d      # services: rmi, coffer, redis
```

Read-only tools answer without payment. Anything priced returns a 402 first —
that is the expected first response, not a failure.

## What it does

- **Paid MCP tools.** Each tool declares a price; the server issues the x402
  challenge, verifies the payment header, and only then runs the handler.
- **Bundled facilitator.** `coffer/` settles payments and keeps receipts in Redis,
  so a replay of the same payment header is rejected.
- **Tool families.** Chain data (`data_mcp_evm`, `data_mcp_solana`), intelligence
  (`data_mcp_intel`), web extraction (`data_mcp_web`) and infra status
  (`data_mcp_infra`) are separate modules over one registration point.
- **Client SDK.** `coffer_client/` is what an agent-side caller uses: build the
  request, pay, retry, and check the receipt.

## How it is put together

```
data_mcp_server.py     MCP entry point, tool registration
data_mcp_*.py          tool families
app/                   x402 middleware, pricing, verification
coffer/                facilitator: verify, settle, receipts
coffer_client/         caller-side payment helper
tests/                 includes bypass and replay cases
```

## Configuration

| Variable | Purpose |
|---|---|
| `X402_WALLET` | generic receiving address; a payment to anything else is rejected |
| `X402_WALLET_SOLANA`, `X402_WALLET_BASE`, `X402_WALLET_ETH`, `X402_WALLET_ARBITRUM`, `X402_WALLET_OPTIMISM`, `X402_WALLET_POLYGON`, `X402_WALLET_TRON` | per-network payees |
| `X402_SELF_FACILITATE` | settle through the bundled facilitator rather than an upstream one |
| `X402_SELF_FACILITATOR_KEY`, `X402_SELF_FACILITATOR_RPC` | signer and node for settlement |
| `REDIS_URL` | receipt store — without it replay protection is off |
| `HELIUS_API_KEY` | Solana RPC for the chain-data tools |
| `PRY_API_URL`, `PRY_API_KEY` | scrape backend used by the web tools |
| `POSTGRES_HOST`, `POSTGRES_DB`, `POSTGRES_PORT` | intelligence store for the intel tools |

Prices and payee addresses are configuration, never hardcoded; a fork with a
different payee is a config change.

## Known limits

- Settlement needs a funded payer and a reachable RPC. Testnet is the sane default
  while you integrate.
- Receipt storage is Redis with no long-term archive; losing the keys loses the
  audit trail.
- Tool prices are set by the operator. This repo does not ship a marketplace or
  discovery service.
- The 402 flow assumes the client can sign. An agent without a wallet can only use
  the free tools.

## Licence

MIT.

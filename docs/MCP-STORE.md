# MCP-STORE — Rug Munch Intelligence (paid MCP surface)

The single, public **MCP store**: five intelligence tools, sold per call over
**x402**, settled by **Coffer** (`127.0.0.1:8001`). Free tier included.

> `x402-gateway` is a **dead byte-identical clone** of this repo (`ARCHIVED.md`,
> scheduled for deletion, never deployed). This repo is the only store.

## Tools and prices

| MCP tool | Price | Free tier | Backend capability (verified live) |
|---|---|---|---|
| `intel.scan` | **$0.01** | 3/day | `POST /api/v2/scanner/token/scan` |
| `scanner.token` | **$0.01** | 3/day | `GET /api/v1/token/{chain}/{address}` |
| `security.wallet` | **$0.02** | 2/day | `GET /api/v1/scoring/wallet/{chain}/{address}` + `POST /api/v1/x402-databus/wallet-labels` |
| `news.search` | **$0.01** | 50/day | `GET /api/v1/news/headlines` (keyword filtered) |
| `exploit.lookup` | **$0.02** | 2/day | `POST /api/v1/rag/v2/search` (`scam_intel`) |

**Why `intel.scan` is $0.01 and not $0.005.** Coffer authorises every
verify/settle against its own `api_access` price — currently **$0.01** — and
rejects under-payment. Charging $0.005 would take the client's signature and
then fail settlement, so the catalog floors every price at Coffer's authorised
amount. `catalog.tools[].intendedUsd` preserves the marketing target. To sell
below $0.01, lower Coffer's `api_access` price first (`X402_PRICING_JSON` on the
`coffer` service) and redeploy — that is a facilitator change, not a store change.

Each tool maps to a **real** backend endpoint. Endpoints that returned
`{"data": null}` (`/api/v2/scanner/token/full/*`, `/risk/*`) or `[]` for every
query (`/api/v1/news/search`) are deliberately **not** used — a paid tool must
never sell an empty payload.

## Endpoints

| Path | Free? | Purpose |
|---|---|---|
| `POST /mcp` | per call | MCP JSON-RPC: `initialize`, `tools/list`, `tools/call` |
| `GET /store/catalog` | free | Machine-readable catalog: price, free tier, schemas, example call |
| `GET /.well-known/x402` | free | Discovery for crawlers/registers |
| `GET /healthz` | free | Liveness + Coffer reachability |
| `GET /store/ledger` | admin | Recent settled receipts (`X-Admin-Key`) |
| `POST /store/keys` | admin | Issue prepaid API key (`X-Admin-Key`) |

## Payment flow (per tool call)

1. Client calls `tools/call` with no credentials.
2. Store returns **HTTP 402** with the challenge in the `PAYMENT-REQUIRED`
   header (base64) **and** in the JSON-RPC error `data.challenge`:
   `accepts[]` = Base mainnet USDC (`eip3009`) + Base Sepolia USDC, `payTo`,
   `maxAmountRequired` (atoms), and a server nonce (`paymentId`).
3. Client signs an EIP-3009 `transferWithAuthorization`, submits the transfer,
   retries with `X-PAYMENT: base64(json({paymentId, txHash, network, asset, amountUsd}))`.
4. Store: replay-claims `paymentId` → `POST coffer /v1/x402/verify` →
   replay-claims `txHash` → `POST coffer /v1/x402/settle` → serves the tool
   result plus a **receipt** (`payment_id`, `tx_hash`, `network`, `amount_usd`).
5. Any failure releases the claims and returns 402/409 — **no data is served
   without settlement.**

Coffer's `verify`→`settle` contract requires a prior successful verify of the
same `payment_id`; settle is idempotent. The store sends the asset **symbol**
(`USDC`), which is what Coffer invoices — sending the contract address is
rejected with `asset ... not supported`.

## Auth models

| Mode | Header | Behaviour |
|---|---|---|
| Pay-per-call | `X-PAYMENT` | verified + settled by Coffer, per call |
| Prepaid | `X-API-Key: rmi_store_…` | debit from prepaid balance, no chain call |
| Free tier | none | server-side counter per identity/tool/UTC-day |
| Identity | `X-WALLET` | wallet-scoped free tier and budget |

Keys are stored as SHA-256 over `STORE_KEY_PEPPER` — never in clear text.
Per-payer daily budget (`STORE_DAILY_BUDGET_USD`, default $1.00) returns 429.

## Run

```bash
# 1. expose the checkout at the path the unit expects
ln -sfn "$REPO_ROOT" /opt/rmi-mcp-store

# 2. root-owned env file (0600) — see the variable table below
install -d -m 0700 /etc/rmi-mcp-store
install -m 0600 "$ENV_FILE" /etc/rmi-mcp-store/env

# 3. install + start
install -m 0644 deploy/rmi-mcp-store.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now rmi-mcp-store
systemctl status rmi-mcp-store --no-pager
curl -s http://127.0.0.1:8026/healthz | jq
```

`REPO_ROOT` is this checkout; `ENV_FILE` is the generated secrets file. Both
live outside the unit so the committed file carries no deployment-specific
paths.

Port **8026** by design: 8000 rmi-backend, 8001 Coffer, 12008 MetaMCP, 9090-9131
MCP bridges. Binds loopback; expose via the existing tunnel/nginx when needed.

Environment (root-owned `env` file, mode 0600):

| Var | Default | Meaning |
|---|---|---|
| `STORE_PORT` / `STORE_HOST` | 8026 / 127.0.0.1 | bind |
| `STORE_DB_PATH` | `/var/lib/rmi-mcp-store/store.db` | SQLite (replay, tiers, ledger, keys) |
| `RMI_BACKEND_URL` | `http://127.0.0.1:8000` | capability source |
| `COFFER_URL` | `http://127.0.0.1:8001` | facilitator |
| `STORE_PAY_TO` | `0x66Aa…a690` | **must equal Coffer's `X402_WALLET`** or reconciliation fails |
| `STORE_ASSET_SYMBOL` | `USDC` | symbol sent to Coffer |
| `STORE_ADMIN_KEY` | — | enables `/store/ledger`, `/store/keys` |
| `STORE_KEY_PEPPER` | — | API-key hashing pepper |
| `STORE_DAILY_BUDGET_USD` | 1.00 | per-payer daily cap |
| `STORE_FREE_TIER` | 1 | set 0 to disable free tier |

## Tests

```bash
.venv/bin/python -m pytest tests/test_store_catalog.py tests/test_store_payments.py -q
```

15 tests, no network egress (capability + facilitator stubbed). Covers the
pricing floor, catalog shape, 402 challenge, free tier, under-payment,
verify failure, asset-symbol payload, replay (payment + tx), double-settle
prevention, daily budget, prepaid debit, invalid key.

## Known blocker — live settlement

Everything up to the chain proof is verified live. A real settlement needs a
**funded payer** (Base USDC, or Base Sepolia USDC for testnet); no funded key
exists on this host, so the settle branch is proven by tests + the live receive
of Coffer's on-chain rejection (`missing EIP-3009 transferWithAuthorization`).
Provide a funded key to close the last mile — no code change required.

# x402 Spec Conformance Contract — rmi-mcp-x402

> Canonical reference for bringing this repo's x402 implementation up to the
> official x402 protocol. Extracted from the local reference implementation at
> `/srv/work/repos/x402-py/x402/` (scheme.py, facilitator.py, middleware.py,
> self_facilitator.py, solana_verifier.py). **Read this file, not the web.**
> The official spec also lives at https://docs.x402.org and the
> `x402-foundation/x402` GitHub repo — but the local reference impl is the
> source of truth for conformance.

## 1. Protocol summary

x402 repurposes HTTP `402 Payment Required` for internet-native payments.

```
client ──GET /paid (no payment)──▶ server
server ──402 + PAYMENT-REQUIRED header + JSON body {x402Version, accepts[]}──▶ client
client pays a facilitator (or self-pays on-chain), gets a verified payment_id
client ──GET /paid + X-Payment-ID: <verified_id>──▶ server
server ──200 (resource)──▶ client
```

No API keys, no accounts. The 402 body advertises what the server accepts;
the client picks an option, pays, and replays with the payment id.

## 2. The 402 response (conformance — MANDATORY)

### 2.1 Headers
| Header | Value | Required |
|---|---|---|
| `PAYMENT-REQUIRED` | Base64( JSON body ) | YES — spec header |
| `X-Payment-Required` | `true` | YES |
| `X-Payment-Options` | `x402` | YES |
| `Content-Type` | `application/json` | YES |

The JSON body is also returned as the response body (decoded), AND base64'd
in the `PAYMENT-REQUIRED` header. Both must carry the same payload.

### 2.2 Body shape — `PaymentRequired`
```jsonc
{
  "x402Version": 1,
  "accepts": [
    {
      "scheme": "exact",                 // "exact" | "upto"
      "network": "eip155:8453",          // CAIP-2 id, NOT bare "base"
      "maxAmountRequired": "1000",       // atomic units, STRING
      "resource": "https://host/api/op", // full resource URL
      "description": "Token risk score",
      "mimeType": "application/json",
      "payToAddress": "0x...",           // server receiving wallet
      "asset": "USDC",
      "extra": { "name": "Base USDC", "version": "2", "operation": "get_token_risk" }
    }
    // ...one entry per accepted (network, asset) pair
  ]
}
```
Optional multi-asset extension adds top-level `networks` (SUPPORTED_NETWORKS)
and `supported_assets` (flattened table) so agents see the full surface.

### 2.3 Replay header (after payment)
| Header | Value |
|---|---|
| `X-Payment-ID` | a server-verified payment id |

Verified ids stay valid for `PAYMENT_VALIDITY_SECONDS` and are **caller-bound**
(bind to client IP / caller_key at record time — replay from a different caller
is rejected).

## 3. Schemes, networks, assets

- **Schemes**: `exact` (exact amount), `upto` (up to amount).
- **Networks** (CAIP-2 — always use the CAIP-2 id, accept legacy names):
  base=`eip155:8453`, ethereum=`eip155:1`, arbitrum=`eip155:42161`,
  optimism=`eip155:10`, polygon=`eip155:137`, solana=`solana:mainnet`,
  tron=`tron:mainnet`.
- **Assets**: USDC, USDT, DAI, ETH, WETH, ARB, SOL, MATIC, POL, TRX (+BTC/LTC/DOGE).
- **Verification methods**: `eip3009` (ERC-20 transferWithAuthorization, USDC),
  `permit2` (USDT/ARB/WETH), `native` (ETH/POL/TRX value transfer), `spl`
  (Solana SPL token), `trc20` (TRON).
- **Decimals**: USDC/USDT=6, DAI/ETH/WETH/ARB/MATIC/POL=18, SOL=9, TRX=6.

## 4. Pricing (server-authoritative — NEVER trust client amount)

- `resolve_price_usd(operation, network, asset)` → server-side USD price.
  Per-network/per-asset overrides via `X402_PRICING[op]["prices"][caip2][asset]`.
- `validate_client_amount(op, client_usd, asset, network)` → `(ok, reason)`.
  Comparison in ATOMIC units so float drift can't be exploited. Underpayment
  refused; overpayment allowed. Non-finite/negative refused.
- `asset_to_atomic(amount_usd, asset, network)` → atomic int using the canonical
  SUPPORTED_ASSETS decimals + usd-per-unit (stablecoins 1:1; volatile assets
  via live-oracle `get_asset_usd_price` with static fallback).

## 5. Facilitator contract (verify + settle)

A facilitator exposes two async operations. `FacilitatorRouter` picks the best
enabled one with health-aware fallback.

### 5.1 verify
```python
async def verify(payment_id, tx_hash, network, asset, amount_usd) -> dict
# returns: {"verified": bool, "reason": str, "settlement": dict|None,
#           "facilitator": str, "signer"?, "amount_atomic"?, "network"?, "asset"?}
```
Default facilitators (priority order, env-overridable):
1. coinbase — `https://x402.org/facilitator/verify` | `/settle`
2. payai — `https://facilitator.payai.network/verify` | `/settle`
3. cloudflare — env URLs (disabled by default)

Payload to external facilitators:
```jsonc
{"paymentId","transactionHash","network","asset",
 "amount": "<atomic str>", "payToAddress", "scheme":"exact","timestamp"}
```
Response: `isValid`/`verified`/`success` → verified.

### 5.2 settle
```python
async def settle(payment_id, tx_hash, network, asset, amount_usd) -> dict
# returns: {"success", "transaction","network","payment_id","facilitator",
#           "settled_at", "block_number","block_hash","confirmations",...}
```

### 5.3 Self-hosted mode (`X402_SELF_FACILITATE=1`) — NO Coinbase dependency
- EVM: `SelfFacilitator` EIP-3009 (transferWithAuthorization) + EIP-7702
  self-verify fallback (poll RPC for receipt, then assert tx paid OUR wallet
  at least the price — ERC-20 transfer() to payTo, or native value to payTo).
- Solana: SPL RPC verifier — `getTransaction` jsonParsed, USDC
  `transferChecked` to the configured Solana wallet.
- TRON: **SCAFFOLDED** — validates structure/price/asset only, reports
  `verified:false reason:"needs_facilitator"`. NEVER claim verified without a
  real verifier. `[REVIEW]` — extension point `register_facilitator(network, impl)`.
- Replay protection: nonce store (SETNX, 24h TTL). Same tx_hash used once.

## 6. Honest verification status (no fake security)
`VERIFICATION_STATUS` per chain family:
- evm: `live` (EIP-7702 self-verify + facilitators)
- solana: `live` (SPL RPC verifier)
- tron: `scaffolded:needs_facilitator` (structure/price/asset validated only)

Scaffolded chains are NEVER reported as live. The `/v1/x402/health` endpoint
exposes this so operators never mistake a scaffold for real settlement.

## 7. Current repo gaps vs this contract (the work to do)

`app/domain/x402/models.py` and `app/domain/x402/middleware.py` are
**NON-SPEC and must be rewritten**:

| # | Current (WRONG) | Spec (CORRECT) |
|---|---|---|
| G1 | `PaymentRequest{tool,chain,user_address}` | `PaymentRequired{x402Version,accepts[]}` |
| G2 | `X-Payment: base64({tx_hash,signature})` | `X-Payment-ID: <verified id>` + `PAYMENT-REQUIRED` header |
| G3 | hand-rolled `create_invoice` w/ `pay_to:"0xRMI_PLATFORM_WALLET"` placeholder + HMAC "signature" | `build_payment_required()` — real `accepts[]` w/ CAIP-2 network, atomic `maxAmountRequired`, real `payToAddress` |
| G4 | bespoke USDC Transfer-log scanner | delegate to `FacilitatorRouter.verify/settle` (EIP-7702/SPL/external) |
| G5 | bare network names ("base","solana") | CAIP-2 ids (`eip155:8453`,`solana:mainnet`) |
| G6 | no server-authoritative pricing / no underpayment rejection | `resolve_price_usd` + `validate_client_amount` (atomic comparison) |
| G7 | no multi-asset accepts surface | `build_multi_asset_accepts()` + `networks`/`supported_assets` |
| G8 | no caller-bound payment-id validity | `X402Handler` records verified ids, caller-bound, TTL'd |
| G9 | no honest scaffolded/live status | `VERIFICATION_STATUS` exposed via `/v1/x402/health` |
| G10 | reimplements x402 from scratch | vendor `x402-py` OR delegate to its public API |

**Preferred fix**: vendor the `x402-py` package (it's already a sibling repo at
`/srv/work/repos/x402-py`) and have `app/domain/x402/` delegate to it, instead
of reimplementing. This single change closes G1–G10 at once.

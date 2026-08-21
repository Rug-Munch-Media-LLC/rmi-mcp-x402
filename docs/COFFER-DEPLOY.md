# Coffer — Deploy to Accept Payments (5-minute runbook)

> Any merchant can run Coffer and accept **USDC** over the HTTP-402 rail today.
> It is both a **resource server** (gate your routes behind a micropayment) and
> a **self/facilitator mesh** (Coinbase · PayAI · Cloudflare · self-hosted).

---

## 1. Configure wallets

Copy the template and set a receiving wallet **at minimum**:

```bash
cp .env.example .env
nano .env
```

| Required | Example | Meaning |
|---|---|---|
| `X402_WALLET` | `0xAbC…` (Base) | default EVM receiving address (`eip155:8453` USDC) |
| `X402_WALLET_SOLANA` | `8Z9…` (base58) | Solana USDC receiving wallet |
| `REDIS_URL` | `redis://redis:6379/0` | nonce/replay store (compose auto-sets this) |

Everything else is optional. **Coffer fails closed**: if no wallet is set, the
service refuses to start with a clear message — it never silently pays a
placeholder address.

> If you self-host the facilitator (default `X402_SELF_FACILITATE=1`), also set
> `X402_SELF_FACILITATOR_KEY` + `X402_SELF_FACILITATOR_RPC` so on-chain
> settlement can be broadcast. Delegate to Coinbase/PayAI/Cloudflare instead by
> setting `X402_SELF_FACILITATE=0` and their `X402_*_ENABLED/VERIFY/SETTLE`.

## 2. Start

```bash
make up                    # docker compose up --build -d  (coffer + redis)
docker compose ps          # both healthy
curl -s http://localhost:8001/v1/x402/health | jq .
```

- `make run` — local uvicorn on `:8001` (no Docker).
- `make down` — stop everything.

## 3. Exercise the 402 flow

```bash
# 1. Unpaid request → HTTP 402 + PAYMENT-REQUIRED challenge
curl -i http://localhost:8001/v1/x402/payment

# 2. Client pays USDC to the offered payToAddress, then replays with the id:
curl -s http://localhost:8001/v1/x402/health \
  -H 'X-Payment-ID: <verified-payment-id>'

# 3. Programmatic verify/settle:
curl -s -X POST http://localhost:8001/v1/x402/verify -H 'content-type: application/json' \
  -d '{"payment_id":"...","tx_hash":"...","network":"eip155:8453","asset":"USDC","amount_usd":0.01}'
curl -s -X POST http://localhost:8001/v1/x402/settle -H 'content-type: application/json' \
  -d '{"payment_id":"...","tx_hash":"...","network":"eip155:8453","asset":"USDC","amount_usd":0.01}'
```

A verified payment id is caller-bound and honoured (`X-Payment-ID`) for
`X402_PAYMENT_TTL` seconds, giving you the **402 → pay → 200** experience.

## 4. Integration — “used by our system and others”

Coffer ships a public facade. Import it and paywall **any** FastAPI route:

```python
import coffer

@coffer.require_payment()
async def expensive_route(request):
    return {"data": "you paid!"}
```

Or gate an entire app with it:

```python
from fastapi import FastAPI
from app.domain.x402.middleware import X402Middleware

app = FastAPI()
app.add_middleware(X402Middleware, free_paths={"/health", "/docs"})
app.include_router(coffer.x402_router)   # /v1/x402/{payment,verify,settle,health}
```

### Config — single source of truth

`app/domain/x402/config.py` is the canonical, validated settings object. It
reads **every** env var Coffer consumes, exposes the CAIP-2 → wallet map
(`settings.network_wallets`) and a `create_app()` uvicorn factory, and
fail-closes at boot on a missing wallet:

```python
from app.domain.x402.config import load_settings
settings = load_settings()          # RuntimeError if no wallet configured
print(settings.network_wallets)     # {"eip155:8453": "0x…", "solana:mainnet": "…"}
```

Existing modules (`service.py`, `store.py`, legacy routers) still read
`os.getenv` directly; config.py is the declaration/validation gate and is
where those reads are converging. The Docker `CMD` runs the factory:

```
uvicorn --factory app.domain.x402.config:create_app --host 0.0.0.0 --port 8001
```

## 5. Healthcheck / ops

- Compose runs a `read_only` rootfs, non-root user, `no-new-privileges`.
- `redis` persists nonce/replay append-only data; the `builder` stage builds the wheel from the repo source.
- Container healthcheck probes `/v1/x402/health` every 30s.

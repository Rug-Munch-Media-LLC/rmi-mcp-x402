#!/usr/bin/env python3
"""Coffer: minimal x402-gated FastAPI route — the one-line paid endpoint.

Run:
    pip install fastapi uvicorn
    PYTHONPATH=. uvicorn examples.fastapi_paid_endpoint:app --reload

Test:
    curl -i http://127.0.0.1:8000/paid
        # -> HTTP/1.1 402 Payment Required
        #    PAYMENT-REQUIRED: <base64 JSON>, X-Payment-Required: true
    curl -i -H "X-Payment-ID: <verified-id>" http://127.0.0.1:8000/paid
        # -> 200 {"data": "paid in one line"}

`@coffer.require_payment(asset=..., network=..., amount=...)` emits the
spec-exact 402 (v1 + v2 shapes, `accepts[]` built from every configured
network/asset via `build_multi_asset_accepts`). A valid, caller-bound, TTL'd
`X-Payment-ID` passes straight through. With `X402_SELF_FACILITATE=1`
(default) settlement runs on your own keys — no Coinbase, no hosted
facilitator.
"""

from __future__ import annotations

from fastapi import FastAPI, Request

# Brand facade: `import coffer; @coffer.require_payment(...)`.
# Falls back to the current repo layout until the `coffer/` package ships.
from _coffer_facade import coffer, require_payment

app = FastAPI(title="Coffer one-line paid endpoint", version="1.0.0")


@app.get("/paid")
@coffer.require_payment(asset="USDC", network="base", amount=0.001)
async def paid_endpoint(request: Request) -> dict[str, str]:
    """Return a resource only after an x402 payment has been verified."""
    return {"data": "paid in one line"}


@app.get("/health")
async def health() -> dict[str, str]:
    """Free path (not gated) so ops can probe liveness."""
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)

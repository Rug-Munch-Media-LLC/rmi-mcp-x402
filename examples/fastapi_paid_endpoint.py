#!/usr/bin/env python3
"""Minimal x402-gated FastAPI route — the one-line paid endpoint.

Run:
    pip install fastapi uvicorn
    PYTHONPATH=. uvicorn examples.fastapi_paid_endpoint:app --reload

Test:
    curl -i http://127.0.0.1:8000/paid
        # -> HTTP/1.1 402 Payment Required
        #    PAYMENT-REQUIRED: <base64 JSON>, X-Payment-Required: true
    curl -i -H "X-Payment-ID: <verified-id>" http://127.0.0.1:8000/paid
        # -> 200 {"data": "paid in one line"}

`@require_payment()` emits the spec-exact 402 (v1 + v2 shapes, `accepts[]`
built from every configured network/asset via `build_multi_asset_accepts`).
A valid, caller-bound, TTL'd `X-Payment-ID` passes straight through.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.domain.x402.middleware import require_payment

app = FastAPI(title="x402 one-line paid endpoint", version="1.0.0")


@app.get("/paid")
@require_payment()
async def paid_endpoint(request: Request) -> dict[str, str]:
    """Return a resource only after an x402 payment has been verified."""
    return {"data": "paid in one line"}


@app.get("/health")
async def health() -> JSONResponse:
    """Free path (not gated) so ops can probe liveness."""
    return JSONResponse({"status": "ok"})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)

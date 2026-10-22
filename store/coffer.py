"""Coffer facilitator client — verify then settle one x402 payment.

Contract measured live (2026-10-23) against Coffer 5.0.0 on :8001:

* ``POST /v1/x402/verify`` then ``POST /v1/x402/settle`` — settle REQUIRES a
  prior successful verify of the same ``payment_id`` from the same caller.
* Body: ``{payment_id, tx_hash, network, asset, amount_usd}``.
* Under-payment is a hard rejection (Coffer's ``api_access`` floor is $0.01),
  so a failed verify means NO resource is served.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger("store.coffer")

_TIMEOUT = 15.0


async def _post(base_url: str, step: str, body: dict[str, Any]) -> dict[str, Any]:
    """Call one facilitator step and normalise the outcome."""
    url = f"{base_url}/v1/x402/{step}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(url, json=body)
    except (httpx.HTTPError, OSError) as exc:
        log.error("coffer_%s_unreachable: %s", step, exc)
        return {"ok": False, "reason": f"facilitator_unreachable:{type(exc).__name__}", "status": 0}
    try:
        data = response.json()
    except ValueError:
        data = {"raw": response.text[:200]}
    if response.status_code != 200:
        reason = str(data.get("detail") or data.get("error") or f"http_{response.status_code}")
        return {"ok": False, "reason": reason[:300], "status": response.status_code, "raw": data}
    return {
        "ok": bool(data.get("ok", data.get("verified", data.get("success", True)))),
        "reason": str(data.get("reason") or ""),
        "tx_hash": str(data.get("tx_hash") or data.get("txHash") or ""),
        "status": 200,
        "raw": data,
    }


def payment_body(
    payment_id: str,
    tx_hash: str,
    network: str,
    asset: str,
    amount_usd: float,
) -> dict[str, Any]:
    """Wire body shared by verify and settle (identical fields on purpose)."""
    return {
        "payment_id": payment_id,
        "tx_hash": tx_hash,
        "network": network,
        "asset": asset,
        "amount_usd": amount_usd,
    }


async def verify(base_url: str, body: dict[str, Any]) -> dict[str, Any]:
    """Ask Coffer to verify an on-chain payment proof."""
    return await _post(base_url, "verify", body)


async def settle(base_url: str, body: dict[str, Any]) -> dict[str, Any]:
    """Ask Coffer to settle a previously verified payment."""
    return await _post(base_url, "settle", body)


async def health(base_url: str) -> dict[str, Any]:
    """Facilitator liveness for the store's own /healthz."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{base_url}/v1/x402/health")
        return {"reachable": True, "status": r.status_code, "body": r.json()}
    except (httpx.HTTPError, OSError, ValueError) as exc:
        return {"reachable": False, "reason": type(exc).__name__}

"""x402 discovery router — spec-required ``/.well-known/x402`` manifest.

Replaces the legacy ``app.routers.x402_enforcement.endpoints.discovery_router``
(pruned per TODO.md: "Prune legacy app/routers/x402_* after domain+verifier own
the path"). The domain layer now owns the discovery surface, built entirely
from canonical domain data so it can never drift from the payment path:

- ``X402_NETWORK_WALLETS``  — pay-to addresses per CAIP-2 network.
- ``X402_PRICING`` / ``resolve_price_usd`` / ``asset_to_atomic`` — server-
  authoritative pricing (never trusts a client amount).
- ``SUPPORTED_NETWORKS`` / ``SUPPORTED_ASSETS`` — canonical network/asset
  tables (mirror ``x402-py:x402/scheme.py``).
- ``VERIFICATION_STATUS`` — honest per-family verification status.

The x402 spec requires ``/.well-known/x402`` to be reachable WITHOUT payment so
agents can enumerate chains/assets before paying; ``create_app`` mounts this
router and the middleware frees ``/.well-known`` (see ``config.free_paths``).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .assets import SUPPORTED_ASSETS, SUPPORTED_NETWORKS, flatten_supported_assets
from .facilitator_config import X402_NETWORK_WALLETS
from .pricing import VERIFICATION_STATUS, X402_PRICING, asset_to_atomic

logger = logging.getLogger("x402_discovery")

router = APIRouter(tags=["x402-discovery"])

__all__ = ["router"]

#: Cache the (expensive) discovery manifest for a short TTL.
_DISCOVERY_CACHE_TTL = 30.0
_discovery_cache: dict[str, Any] | None = None
_discovery_cache_time = 0.0


def _build_discovery_response() -> dict[str, Any]:
    """Build the spec-compliant ``/.well-known/x402`` manifest.

    Lists every supported (network, asset) pair with its server-authoritative
    price (atomic units) and pay-to address, plus the honest verification
    status per network family.
    """
    networks: list[dict[str, Any]] = []
    for net in SUPPORTED_NETWORKS:
        nid = net["network"]
        networks.append(
            {
                "network": nid,
                "name": net.get("name", ""),
                "family": net.get("family", ""),
                "verification_status": VERIFICATION_STATUS.get(net.get("family", ""), "unknown"),
                "payToAddress": X402_NETWORK_WALLETS.get(nid, ""),
            }
        )

    # Per-operation pricing: for each priced op, quote every supported asset
    # in atomic units (server-authoritative, never client-supplied).
    operations: dict[str, Any] = {}
    for op, op_table in X402_PRICING.items():
        if op == "*" or not isinstance(op_table, dict):
            continue
        price_usd = float(op_table.get("price_usd", 0.0))
        assets: list[dict[str, Any]] = []
        for nid, net_assets in SUPPORTED_ASSETS.items():
            for asset, meta in net_assets.items():
                try:
                    atomic = asset_to_atomic(price_usd, asset, nid)
                except ValueError:
                    continue
                assets.append(
                    {
                        "network": nid,
                        "asset": asset,
                        "maxAmountRequired": str(atomic),
                        "contract": meta.get("contract", ""),
                        "verification": meta.get("verification", ""),
                    }
                )
        operations[op] = {
            "price_usd": price_usd,
            "description": op_table.get("description", ""),
            "accepts": assets,
        }

    return {
        "x402Version": 1,
        "name": "Coffer — self-hosted x402 payment facilitator",
        "networks": networks,
        "supported_assets": flatten_supported_assets(),
        "operations": operations,
        "verification_status": dict(VERIFICATION_STATUS),
    }


@router.get("/.well-known/x402")
async def x402_discovery() -> JSONResponse:
    """x402 discovery manifest — reachable without payment (spec-required)."""
    global _discovery_cache, _discovery_cache_time
    now = time.time()
    if _discovery_cache is not None and (now - _discovery_cache_time) < _DISCOVERY_CACHE_TTL:
        return JSONResponse(content=_discovery_cache)
    _discovery_cache = _build_discovery_response()
    _discovery_cache_time = now
    return JSONResponse(content=_discovery_cache)


@router.get("/api/v1/x402/trial-status")
async def trial_status() -> JSONResponse:
    """Trial availability per priced operation (server-authoritative)."""
    trials: dict[str, Any] = {}
    for op, op_table in X402_PRICING.items():
        if op == "*" or not isinstance(op_table, dict):
            continue
        trials[op] = {
            "max": int(op_table.get("trial_free", 0)),
            "description": op_table.get("description", ""),
        }
    return JSONResponse(content={"tools_with_trials": len(trials), "trials": trials})


@router.get("/api/v1/x402/revenue")
async def revenue() -> JSONResponse:
    """Read-only revenue/usage summary from the in-process payment store.

    The legacy Redis-backed revenue counters were pruned with the legacy
    routers; the domain layer reports the live verified-payment store instead.
    """
    try:
        from .store import get_default_store

        store = get_default_store()
        return JSONResponse(
            content={
                "active_payments": len(store),
                "payment_validity_seconds": int(getattr(store, "ttl_seconds", 0) or 0),
                "note": "in-process verified-payment store; no Redis counters",
            }
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("revenue read error: %s", e)
        return JSONResponse(status_code=503, content={"error": "revenue unavailable"})


@router.get("/api/v1/x402/analytics/pricing-suggestions")
async def pricing_suggestions() -> JSONResponse:
    """Pricing suggestions from the server-authoritative price table.

    The legacy conversion-rate algorithm depended on Redis usage counters that
    were pruned with the legacy routers. The domain layer reports the current
    configured prices so operators can review them directly.
    """
    suggestions: list[dict[str, Any]] = []
    for op, op_table in X402_PRICING.items():
        if op == "*" or not isinstance(op_table, dict):
            continue
        suggestions.append(
            {
                "operation": op,
                "price_usd": float(op_table.get("price_usd", 0.0)),
                "description": op_table.get("description", ""),
            }
        )
    return JSONResponse(content={"suggestions": suggestions, "count": len(suggestions)})

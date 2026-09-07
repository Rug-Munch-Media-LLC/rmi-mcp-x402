# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Rug Munch Media LLC
"""x402 paid offerings router — the three sellable verdicts, x402-gated.

Mounted at ``/v1/*`` (via ``app.routers.x402``):

- ``POST /v1/verdict/{token}``  — Rug Verdict ($0.05 quick / $0.50 deep)
- ``GET  /v1/dossier/{wallet}`` — Deployer Dossier ($0.25-$0.50)
- ``GET  /v1/cluster/{wallet}`` — Cluster Attribution ($0.05-$0.20)

Every endpoint is FAIL-CLOSED x402-gated: the request must carry an
``X-Payment-ID`` that (a) exists in the caller-bound verified-payment store
and (b) is re-verified against the x402-coffer facilitator
(:class:`CofferFacilitatorClient`) on EVERY call. Any transport error,
timeout, 5xx or ``verified: false`` from coffer resolves to
``402 Payment Required`` — an unreachable facilitator never lets a paid
response through unverified.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from .adapters import HEADER_X_PAYMENT_ID, caller_key_from_request
from .body import payment_required_body, payment_required_headers
from .facilitator_client import get_facilitator_client
from .offerings import build_cluster_attribution, build_deployer_dossier, build_rug_verdict
from .store import get_default_store

__all__ = ["router", "require_verified_payment"]

router = APIRouter(prefix="/v1", tags=["x402-offerings"])

log = logging.getLogger("x402_offerings")


def _payment_required(reason: str) -> HTTPException:
    body = payment_required_body()
    return HTTPException(
        status_code=402,
        detail={"error": "Payment required", "code": "PAYMENT_REQUIRED", "reason": reason},
        headers=payment_required_headers(body),
    )


async def require_verified_payment(request: Request) -> dict[str, Any]:
    """Coffer-facilitated gate: 402 unless coffer verifies the payment id.

    Two-stage check, both fail-closed:
    1. The ``X-Payment-ID`` must have a caller-bound record in the verified
       payment store (recorded by ``POST /v1/x402/verify``).
    2. The id is re-verified against the coffer facilitator over HTTP; any
       failure (down, erroring, refusing) -> 402, never allow-through.
    """
    payment_id = request.headers.get(HEADER_X_PAYMENT_ID.lower(), "")
    caller_key = caller_key_from_request(request)
    record = get_default_store().get(payment_id) if payment_id else None
    if record is None or (record.get("caller_key") and record.get("caller_key") != caller_key):
        log.debug("x402_offering_payment_required", extra={"reason": "no_verified_payment"})
        raise _payment_required(
            "Verified X-Payment-ID required — verify via POST /v1/x402/verify, "
            "then replay with the X-Payment-ID header set to the returned payment_id."
        )

    verdict = await get_facilitator_client().verify(
        payment_id=payment_id,
        tx_hash=str(record.get("tx_hash") or ""),
        network=str(record.get("network") or ""),
        asset=str(record.get("asset") or ""),
        amount_usd=float(record.get("amount_usd") or 0.0),
    )
    if not verdict.get("verified"):
        # Fail closed: coffer down, erroring, or refusing the payment -> 402.
        log.debug("x402_offering_payment_required", extra={"reason": "facilitator_refused"})
        raise _payment_required(str(verdict.get("reason") or "payment not verified by facilitator"))

    return record


@router.post("/verdict/{token}")
async def rug_verdict(
    token: str,
    chain: str = "solana",
    _payment: dict[str, Any] = Depends(require_verified_payment),
) -> dict[str, Any]:
    """Explainable rug verdict for a token (paid offering #1)."""
    return await build_rug_verdict(token, chain)


@router.get("/dossier/{wallet}")
async def deployer_dossier(
    wallet: str,
    chain: str = "solana",
    _payment: dict[str, Any] = Depends(require_verified_payment),
) -> dict[str, Any]:
    """Lifetime deployer dossier for a wallet (paid offering #2)."""
    return await build_deployer_dossier(wallet, chain)


@router.get("/cluster/{wallet}")
async def cluster_attribution(
    wallet: str,
    chain: str = "solana",
    _payment: dict[str, Any] = Depends(require_verified_payment),
) -> dict[str, Any]:
    """4-heuristic wallet cluster attribution (paid offering #3)."""
    return await build_cluster_attribution(wallet, chain)

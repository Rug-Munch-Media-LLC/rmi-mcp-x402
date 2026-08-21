"""x402 HTTP router — spec-compliant 402-flow endpoints.

Exposes the official x402 HTTP transport surface:

- ``GET  /v1/x402/payment``  — 402 challenge: the spec header set
  (``PAYMENT-REQUIRED`` base64 + ``X-Payment-Required`` + ``X-Payment-Options``)
  and the ``{x402Version:1, accepts:[...]}`` body for a not-yet-paid caller.
- ``POST /v1/x402/verify``   — delegate a payment proof to the
  ``FacilitatorRouter`` (from :mod:`app.domain.x402.service`, agent3-owned);
  on success the verified id is recorded caller-bound + TTL'd in the shared
  :class:`PaymentStore` so replay with ``X-Payment-ID`` passes the middleware.
- ``POST /v1/x402/settle``   — delegate settlement to the FacilitatorRouter.
- ``GET  /v1/x402/health``   — honest acceptance/verification status.

Holds NO verification logic itself — it only wires the middleware's payment
store to the facilitator layer.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.domain.x402.middleware import (
    HEADER_PAYMENT_REQUIRED,
    HEADER_X_PAYMENT_ID,
    HEADER_X_PAYMENT_OPTIONS,
    HEADER_X_PAYMENT_REQUIRED,
    PAYMENT_VALIDITY_SECONDS,
    PaymentStore,
    caller_key_from_request,
    get_default_store,
    payment_required_body,
    payment_required_headers,
)

router = APIRouter(prefix="/v1/x402", tags=["x402"])

VERIFICATION_STATUS = {
    "evm": "live",
    "solana": "live",
    "tron": "scaffolded:needs_facilitator",
}


def _get_facilitator_router() -> Any:
    """Return agent3's ``FacilitatorRouter`` (imported lazily from service.py).

    Imported lazily — not at module load — so this router keeps working and
    type-checks clean if the interface is still landing in ``service.py``.
    """
    from app.domain.x402.service import FacilitatorRouter  # type: ignore[attr-defined]

    return FacilitatorRouter()


def _get_store() -> PaymentStore:
    """Return the shared, process-wide verified-payment store."""
    return get_default_store()


def _402_challenge(resource: str = "") -> JSONResponse:
    """Build a spec-compliant 402 response for an unpaid caller."""
    body = payment_required_body(resource=resource, operation="api_access")
    headers = {
        HEADER_PAYMENT_REQUIRED: payment_required_headers(body)[HEADER_PAYMENT_REQUIRED],
        HEADER_X_PAYMENT_REQUIRED: "true",
        HEADER_X_PAYMENT_OPTIONS: "x402",
    }
    return JSONResponse(status_code=402, content=body, headers=headers)


async def _read_json(request: Request) -> dict[str, Any]:
    try:
        raw = await request.body()
        return json.loads(raw or b"{}")
    except (json.JSONDecodeError, RuntimeError):
        return {}


def _amount(payload: dict[str, Any]) -> float:
    try:
        return float(payload.get("amount_usd", payload.get("amountUsd", 0.0)) or 0.0)
    except (TypeError, ValueError):
        return 0.0


@router.get("/payment")
async def payment_challenge() -> JSONResponse:
    """402 challenge: advertise what this server accepts for a paid call."""
    return _402_challenge()


@router.post("/verify")
async def verify(request: Request) -> JSONResponse:
    """Verify a payment proof via the FacilitatorRouter; record it on success."""
    payload = await _read_json(request)
    payment_id = str(payload.get("payment_id") or payload.get("paymentId") or "")
    tx_hash = str(payload.get("tx_hash") or payload.get("transactionHash") or "")
    network = str(payload.get("network") or "")
    asset = str(payload.get("asset") or "")
    amount_usd = _amount(payload)

    if not payment_id or not tx_hash:
        return JSONResponse(
            status_code=400,
            content={"x402Version": 1, "error": "payment_id and tx_hash are required"},
        )

    result = await _get_facilitator_router().verify(
        payment_id,
        tx_hash,
        network,
        asset,
        amount_usd,
    )

    if not result.get("verified"):
        body = payment_required_body(operation="api_access")
        headers = payment_required_headers(body)
        return JSONResponse(
            status_code=402,
            content={
                "x402Version": 1,
                "error": result.get("reason", "payment verification failed"),
                **body,
            },
            headers=headers,
        )

    _get_store().record(
        payment_id=payment_id,
        caller_key=caller_key_from_request(request),
        tx_hash=tx_hash,
        network=network or str(result.get("network") or ""),
        asset=asset or str(result.get("asset") or ""),
        amount_usd=float(result.get("amount_usd") or amount_usd),
        facilitator=str(result.get("facilitator") or ""),
        verified=True,
    )
    return JSONResponse(
        content={
            "verified": True,
            "reason": result.get("reason", ""),
            "facilitator": result.get("facilitator", ""),
            "payment_id": payment_id,
            "transaction": tx_hash,
            "network": network or result.get("network", ""),
            "asset": asset or result.get("asset", ""),
            "amount_atomic": result.get("amount_atomic", ""),
            "signer": result.get("signer", ""),
            "settlement": result.get("settlement"),
            "replay": {"header": HEADER_X_PAYMENT_ID, "value": payment_id},
        }
    )


@router.post("/settle")
async def settle(request: Request) -> JSONResponse:
    """Settle a verified payment via the FacilitatorRouter."""
    payload = await _read_json(request)
    payment_id = str(payload.get("payment_id") or payload.get("paymentId") or "")
    tx_hash = str(payload.get("tx_hash") or payload.get("transactionHash") or "")
    network = str(payload.get("network") or "")
    asset = str(payload.get("asset") or "")
    amount_usd = _amount(payload)

    if not payment_id or not tx_hash:
        return JSONResponse(
            status_code=400,
            content={"error": "payment_id and tx_hash are required"},
        )

    settlement = await _get_facilitator_router().settle(
        payment_id,
        tx_hash,
        network,
        asset,
        amount_usd,
    )
    return JSONResponse(content=settlement)


@router.get("/health")
async def health() -> JSONResponse:
    """Honest acceptance + verification status. Scaffolded chains are NEVER
    reported as fully live."""
    store = _get_store()
    return JSONResponse(
        content={
            "status": "ok",
            "x402Version": 1,
            "verification_status": VERIFICATION_STATUS,
            "payment_validity_seconds": PAYMENT_VALIDITY_SECONDS,
            "active_payments": len(store),
            "replay_header": HEADER_X_PAYMENT_ID,
            "offer_header": HEADER_PAYMENT_REQUIRED,
        }
    )

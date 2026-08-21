"""x402 payment middleware — spec-compliant HTTP 402 gating.

Implements the OFFICIAL x402 402-flow per the repo's X402-SPEC-CONTRACT §2 and
the HTTP transport specs (``docs/x402-official-spec/transports-v1__http.md`` /
``transports-v2__http.md``):

- Unpaid requests for a gated path receive::

      HTTP/1.1 402 Payment Required
      Content-Type: application/json
      PAYMENT-REQUIRED: <base64(JSON body)>
      X-Payment-Required: true
      X-Payment-Options: x402

  and a JSON body ``{x402Version: 1, accepts: [...]}`` built via the mirrored
  canonical ``build_payment_required`` / ``build_multi_asset_accepts``
  (multi-asset, CAIP-2 network ids, atomic ``maxAmountRequired``, configured
  ``payToAddress`` — NOT a hardcoded placeholder).

- After paying via a facilitator, the client replays with ``X-Payment-ID``;
  the id is checked against a caller-bound (client IP), TTL'd store (mirrors
  the canonical ``X402Handler`` semantics — ``PAYMENT_VALIDITY_SECONDS``).

- ``free_paths`` subsets (``/health``, ``/docs``) pass through.

NOTE on the canonical reference: the local ``x402-py`` reference is NOT
vendored into this repo's dependency set (see ``pyproject.toml``), and the
installed PyPI ``x402`` (v2.14.0) exposes a different API. Per the conformance
contract these helpers MIRROR the ``x402-py`` canonical implementations exactly
so the emitted wire format is byte-for-byte the spec shape.
"""

from __future__ import annotations

import base64
import functools
import inspect
import json
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)

# ── Spec headers on the 402 response ────────────────────────────────────────
HEADER_PAYMENT_REQUIRED = "PAYMENT-REQUIRED"
HEADER_X_PAYMENT_REQUIRED = "X-Payment-Required"
HEADER_X_PAYMENT_OPTIONS = "X-Payment-Options"
HEADER_CONTENT_TYPE = "Content-Type"
# Replay header a paid client presents on subsequent requests.
HEADER_X_PAYMENT_ID = "X-Payment-ID"

# TTL for verified payment ids (mirrors x402-py PAYMENT_VALIDITY_SECONDS).
PAYMENT_VALIDITY_SECONDS: int = int(os.getenv("X402_PAYMENT_TTL", "3600"))

# x402 version this implementation emits.
X402_VERSION = 1

# ── Canonical acceptance surface (CAIP-2 ids, per X402-SPEC-CONTRACT §3) ────
LEGACY_TO_CAIP2: dict[str, str] = {
    "base": "eip155:8453",
    "ethereum": "eip155:1",
    "arbitrum": "eip155:42161",
    "optimism": "eip155:10",
    "polygon": "eip155:137",
    "solana": "solana:mainnet",
    "tron": "tron:mainnet",
}

ASSET_DECIMALS: dict[str, int] = {
    "USDC": 6,
    "USDT": 6,
    "DAI": 18,
    "ETH": 18,
    "WETH": 18,
    "ARB": 18,
    "MATIC": 18,
    "POL": 18,
    "SOL": 9,
    "TRX": 6,
}

# Which assets each CAIP-2 network accepts for payment.
NETWORK_ASSETS: dict[str, list[str]] = {
    "eip155:8453": ["USDC", "USDT", "ETH", "WETH", "DAI"],
    "eip155:1": ["USDC", "USDT", "ETH", "WETH"],
    "eip155:42161": ["USDC", "USDT", "ARB", "WETH"],
    "eip155:10": ["USDC", "USDT", "WETH"],
    "eip155:137": ["USDC", "USDT", "POL", "MATIC"],
    "solana:mainnet": ["USDC", "USDT", "SOL"],
    "tron:mainnet": ["USDC", "USDT", "TRX"],
}

# Server-authoritative pricing (USD) per operation — NEVER trust the client.
X402_PRICING: dict[str, dict[str, Any]] = {
    "api_access": {"price_usd": 0.01, "description": "x402 paid API call"},
    "get_token_risk": {"price_usd": 0.01, "description": "Token risk score"},
    "get_wallet_analysis": {"price_usd": 0.01, "description": "Wallet analysis"},
    "generate_report": {"price_usd": 0.10, "description": "AI research report"},
}

# ── Verified-payment store (X402Handler-style: caller-bound + TTL) ──────────
DEFAULT_PRICE_USD = 0.01


def normalize_network_id(network: str) -> str:
    """Map a legacy network name to its CAIP-2 id (spec §3)."""
    return LEGACY_TO_CAIP2.get(network.strip().lower(), network.strip())


def _pay_to_address(caip2: str) -> str:
    """Resolve the configured receiving wallet for a CAIP-2 network."""
    family = "solana" if caip2.startswith("solana:") else caip2
    key = f"X402_WALLET_{family.replace(':', '_').upper()}"
    return os.getenv(key) or os.getenv("X402_WALLET") or ""


def asset_to_atomic(amount_usd: float, asset: str, network: str) -> int:
    """Convert a USD price to atomic units for ``asset`` on ``network``.

    Stablecoins are USD 1:1 at their fixed decimals. Volatile assets fall back
    to a static USD-per-unit estimate (a live oracle would refine this; see
    contract §4). Atomic so float drift can't be exploited server-side.
    """
    decimals = ASSET_DECIMALS.get(asset.upper(), 6)
    unit = 10**decimals
    usd_per_unit = _usd_per_unit(asset.upper())
    if usd_per_unit is None or usd_per_unit <= 0:
        return int(round(amount_usd * unit))
    return int(round(amount_usd / usd_per_unit * unit))


def _usd_per_unit(asset: str) -> float | None:
    """Static USD-per-unit for volatile assets; None means USD-pegged."""
    stable = {"USDC", "USDT", "DAI", "POL", "MATIC", "TRX"}
    if asset in stable:
        return 1.0
    fallback = {"ETH": 3000.0, "WETH": 3000.0, "ARB": 1.0, "SOL": 150.0}
    return fallback.get(asset)


def _price_usd(operation: str) -> float:
    info = X402_PRICING.get(operation) or X402_PRICING.get("api_access")
    return float((info or {}).get("price_usd", DEFAULT_PRICE_USD))


def _option(
    *,
    operation: str,
    resource: str,
    description: str,
    caip2: str,
    asset: str,
) -> dict[str, Any]:
    """One ``accepts[]`` entry (exact scheme, atomic amount, CAIP-2 id)."""
    return {
        "scheme": "exact",
        "network": caip2,
        "maxAmountRequired": str(asset_to_atomic(_price_usd(operation), asset, caip2)),
        "resource": resource,
        "description": description,
        "mimeType": "application/json",
        "payToAddress": _pay_to_address(caip2),
        "asset": asset.upper(),
        "extra": {"name": asset.upper(), "version": "2", "operation": operation},
    }


def build_multi_asset_accepts(
    operation: str,
    resource: str = "",
    description: str = "",
) -> list[dict[str, Any]]:
    """Multi-asset ``accepts[]`` covering every quotable (network, asset)."""
    if not description:
        info = X402_PRICING.get(operation) or X402_PRICING.get("api_access")
        description = str((info or {}).get("description") or operation or "api_access")
    accepts: list[dict[str, Any]] = []
    for caip2, assets in NETWORK_ASSETS.items():
        for asset in assets:
            if _pay_to_address(caip2):
                # Only advertise networks we have a receiving wallet for.
                accepts.append(
                    _option(
                        operation=operation,
                        resource=resource,
                        description=description,
                        caip2=caip2,
                        asset=asset,
                    )
                )
    if not accepts:
        base = normalize_network_id("base")
        accepts.append(
            _option(
                operation=operation,
                resource=resource,
                description=description,
                caip2=base,
                asset="USDC",
            )
        )
    return accepts


def build_payment_required(
    operation: str,
    resource: str = "",
    multi_asset: bool = True,
) -> dict[str, Any]:
    """Build the spec-compliant ``{x402Version:1, accepts:[...]}`` body."""
    accepts = build_multi_asset_accepts(operation, resource=resource)
    body: dict[str, Any] = {"x402Version": X402_VERSION, "accepts": accepts}
    if multi_asset:
        # Additive multi-chain surface so agents see every network/asset.
        body["networks"] = [
            {"id": caip2, "assets": assets} for caip2, assets in NETWORK_ASSETS.items()
        ]
        body["supported_assets"] = [
            {"network": caip2, "asset": asset}
            for caip2, assets in NETWORK_ASSETS.items()
            for asset in assets
        ]
    return body


def _encode_payment_required_body(body: dict[str, Any]) -> str:
    """Base64-encode the PaymentRequired body (canonical wire format)."""
    return base64.b64encode(json.dumps(body).encode("utf-8")).decode("ascii")


def payment_required_body(resource: str = "", operation: str = "api_access") -> dict[str, Any]:
    """The spec body for a generic 402 challenge."""
    return build_payment_required(operation or "api_access", resource=resource, multi_asset=True)


def payment_required_headers(body: dict[str, Any]) -> dict[str, str]:
    """Spec header set for a 402 response carrying ``body``."""
    return {
        HEADER_PAYMENT_REQUIRED: _encode_payment_required_body(body),
        HEADER_X_PAYMENT_REQUIRED: "true",
        HEADER_X_PAYMENT_OPTIONS: "x402",
    }


def is_free_path(path: str, free_paths: set[str]) -> bool:
    """Prefix match: ``/v1/health`` also frees ``/v1/health/details``."""
    return any(path.startswith(prefix) for prefix in free_paths)


class PaymentStore:
    """In-memory verified-payment store, caller-bound + TTL'd.

    Mirrors the canonical ``X402Handler`` replay contract:

    - ``is_paid(payment_id, caller_key)`` is True only when the id was verified,
      has not exceeded ``PAYMENT_VALIDITY_SECONDS``, and — when it was recorded
      with a caller key — the replaying caller matches it.
    - A record without an ``expires_at`` (pre-hardening) fails closed.
    """

    def __init__(self, *, validity_seconds: int = PAYMENT_VALIDITY_SECONDS) -> None:
        self.validity_seconds = validity_seconds
        self._records: dict[str, dict[str, Any]] = {}

    def record(
        self,
        *,
        payment_id: str,
        caller_key: str = "",
        tx_hash: str = "",
        network: str = "",
        asset: str = "",
        amount_usd: float = 0.0,
        facilitator: str = "",
        verified: bool = True,
    ) -> None:
        """Record a verified payment id bound to ``caller_key`` for the TTL."""
        self._records[payment_id] = {
            "payment_id": payment_id,
            "tx_hash": tx_hash,
            "network": network,
            "asset": asset,
            "amount_usd": amount_usd,
            "facilitator": facilitator,
            "verified": verified,
            "caller_key": caller_key or "",
            "expires_at": (
                datetime.now(UTC) + timedelta(seconds=self.validity_seconds)
            ).isoformat(),
            "verified_at": datetime.now(UTC).isoformat(),
        }

    def is_paid(self, payment_id: str, caller_key: str = "") -> bool:
        """True when the id is verified, unexpired and caller-bound."""
        if not payment_id:
            return False
        rec = self._records.get(payment_id)
        if rec is None or not rec.get("verified"):
            return False
        expires_at = rec.get("expires_at")
        if not expires_at:
            return False  # no expiry → fail closed
        try:
            if datetime.fromisoformat(expires_at) <= datetime.now(UTC):
                return False
        except ValueError:
            return False
        stored_caller = rec.get("caller_key") or ""
        if stored_caller and (not caller_key or stored_caller != caller_key):
            return False
        return True

    def get(self, payment_id: str) -> dict[str, Any] | None:
        return self._records.get(payment_id)

    def __len__(self) -> int:
        return len(self._records)


_default_store: PaymentStore | None = None


def get_default_store() -> PaymentStore:
    """Return (creating once) the process-wide verified-payment store."""
    global _default_store
    if _default_store is None:
        _default_store = PaymentStore()
    return _default_store


def set_default_store(store: PaymentStore | None) -> None:
    """Override (or clear, with ``None``) the process-wide payment store."""
    global _default_store
    _default_store = store


# ASGI structural types (framework-agnostic).
Scope = dict[str, Any]
Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


def caller_key_from_scope(scope: Scope) -> str:
    """Best-effort caller identity (client IP) from an ASGI scope."""
    client = scope.get("client")
    if isinstance(client, tuple | list) and client:
        return str(client[0])
    return ""


def caller_key_from_request(request: Request | None) -> str:
    """Best-effort caller identity for FastAPI route handlers."""
    if request is not None and request.client is not None and request.client.host:
        return str(request.client.host)
    return ""


class X402Middleware:
    """ASGI middleware gating routes behind x402 payments.

    Use with FastAPI / Starlette::

        app.add_middleware(X402Middleware, free_paths={"/health", "/docs"})

    Requests whose path starts with any ``free_paths`` entry pass through.
    Every other request must present a verified ``X-Payment-ID`` header;
    unpaid requests get HTTP 402 with the spec header set + JSON body.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        free_paths: set[str] | None = None,
        store: PaymentStore | None = None,
    ) -> None:
        self.app = app
        self.free_paths: set[str] = set(free_paths or set())
        self.store = store or get_default_store()

    def _is_free(self, path: str) -> bool:
        return is_free_path(path, self.free_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path", ""))
        if self._is_free(path):
            await self.app(scope, receive, send)
            return
        raw_headers = scope.get("headers") or []
        headers = {
            str(name.decode("latin-1")).lower(): str(value.decode("latin-1"))
            for name, value in raw_headers
        }
        caller_key = caller_key_from_scope(scope)
        payment_id = headers.get(HEADER_X_PAYMENT_ID.lower(), "")
        if self.store.is_paid(payment_id, caller_key=caller_key):
            await self.app(scope, receive, send)
            return
        body = payment_required_body(resource=path)
        response_headers = payment_required_headers(body)
        await send(
            {
                "type": "http.response.start",
                "status": 402,
                "headers": [
                    (HEADER_CONTENT_TYPE.encode("latin-1"), b"application/json"),
                    *[
                        (str(k).lower().encode("latin-1"), str(v).encode("latin-1"))
                        for k, v in response_headers.items()
                    ],
                ],
            }
        )
        await send({"type": "http.response.body", "body": json.dumps(body).encode()})


def _is_paid_for(request: Request, store: PaymentStore) -> bool:
    """True when the request carries a verified, caller-bound payment id."""
    payment_id = request.headers.get(HEADER_X_PAYMENT_ID.lower(), "")
    return store.is_paid(payment_id, caller_key=caller_key_from_request(request))


def require_payment(
    *,
    store: PaymentStore | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator: gate a FastAPI route handler behind an x402 payment.

    Paid requests (valid ``X-Payment-ID``) pass through; unpaid requests get a
    spec-compliant ``JSONResponse(status_code=402)`` with the full header set.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        def _gate(args: tuple[Any, ...], kwargs: dict[str, Any]) -> bool | JSONResponse:
            active_store = store or get_default_store()
            request = _find_request(args, kwargs)
            if request is not None and _is_paid_for(request, active_store):
                return True
            body = payment_required_body()
            return JSONResponse(
                status_code=402, content=body, headers=payment_required_headers(body)
            )

        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                result = _gate(args, kwargs)
                if result is True:
                    return await fn(*args, **kwargs)
                return result

            return async_wrapper

        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            result = _gate(args, kwargs)
            if result is True:
                return fn(*args, **kwargs)
            return result

        return sync_wrapper

    return decorator


def _find_request(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Request | None:
    """Locate a FastAPI/Starlette ``Request`` among handler args."""
    for candidate in (*args, *kwargs.values()):
        if isinstance(candidate, Request):
            return candidate
    return None


__all__ = [
    "ASGIApp",
    "HEADER_PAYMENT_REQUIRED",
    "HEADER_X_PAYMENT_ID",
    "HEADER_X_PAYMENT_OPTIONS",
    "HEADER_X_PAYMENT_REQUIRED",
    "PAYMENT_VALIDITY_SECONDS",
    "PaymentStore",
    "X402Middleware",
    "asset_to_atomic",
    "build_multi_asset_accepts",
    "build_payment_required",
    "caller_key_from_request",
    "get_default_store",
    "is_free_path",
    "normalize_network_id",
    "payment_required_body",
    "payment_required_headers",
    "require_payment",
    "set_default_store",
]

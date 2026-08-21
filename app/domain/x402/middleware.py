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

  and a JSON body ``{x402Version: 1, accepts: [...]}`` built via
  :func:`build_payment_required` / :func:`build_multi_asset_accepts`
  (multi-asset, CAIP-2 network ids, atomic ``maxAmountRequired`` strings,
  asset SYMBOL + ``extra.contract`` address, configured ``payToAddress`` —
  NOT a hardcoded placeholder).

- After paying via a facilitator, the client replays with ``X-Payment-ID``;
  the id is checked against a caller-bound (client IP), TTL'd store (mirrors
  the canonical ``X402Handler`` semantics — ``PAYMENT_VALIDITY_SECONDS``).

- ``free_paths`` subsets (``/health``, ``/docs``) pass through.

Header convention (fleet-canonical, ONE convention across the domain layer):

- replay  : ``X-Payment-ID: <verified payment id>`` (contract §2.3)
- offer   : ``PAYMENT-REQUIRED`` (base64 body) + ``X-Payment-Required: true``
  + ``X-Payment-Options: x402`` (contract §2.1)

The legacy ``X-Payment: base64({tx_hash, signature})`` client header and the
v2 ``PAYMENT-SIGNATURE`` header are NOT consumed on the v1 wire — payment
proofs go through ``POST /v1/x402/verify`` and replay uses ``X-Payment-ID``.
TODO(v2): additive — accept ``PAYMENT-SIGNATURE`` payloads once the gateway
migrates to x402Version=2 (see ``models.X402_VERSION``).

Single source of truth: network/asset metadata comes from
``models.SUPPORTED_NETWORKS`` / ``models.SUPPORTED_ASSETS`` (canonical,
mirrors ``x402-py:x402/scheme.py``), pricing + wallets from ``service``
(``resolve_price_usd``, ``X402_NETWORK_WALLETS``) — so middleware, router and
service can never drift apart. The local ``x402-py`` reference is not
vendored (see ``pyproject.toml``); these helpers mirror its wire output.
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

from .models import SUPPORTED_ASSETS, SUPPORTED_NETWORKS, flatten_supported_assets
from .service import (
    X402_NETWORK_WALLETS,
    X402_PRICING,
    X402_WALLET,
    asset_to_atomic,
    resolve_price_usd,
)

log = logging.getLogger(__name__)

# ── Spec headers on the 402 response ────────────────────────────────────────
HEADER_PAYMENT_REQUIRED = "PAYMENT-REQUIRED"
HEADER_X_PAYMENT_REQUIRED = "X-Payment-Required"
HEADER_X_PAYMENT_OPTIONS = "X-Payment-Options"
HEADER_CONTENT_TYPE = "Content-Type"
# Replay header a paid client presents on subsequent requests.
HEADER_X_PAYMENT_ID = "X-Payment-ID"

#: TTL for verified payment ids (mirrors x402-py ``PAYMENT_VALIDITY_SECONDS``;
#: env ``X402_PAYMENT_TTL`` overrides).
PAYMENT_VALIDITY_SECONDS: int = int(os.getenv("X402_PAYMENT_TTL", "3600"))

# x402 version this implementation emits (v1 wire; TODO(v2) additive).
X402_VERSION = 1

# ── Canonical acceptance surface (CAIP-2 ids, per X402-SPEC-CONTRACT §3) ────
# Legacy bare name -> CAIP-2 id for INPUT normalisation only; everything this
# module EMITS is CAIP-2 (canonical tables live in models.py).
LEGACY_TO_CAIP2: dict[str, str] = {
    "base": "eip155:8453",
    "ethereum": "eip155:1",
    "arbitrum": "eip155:42161",
    "optimism": "eip155:10",
    "polygon": "eip155:137",
    "solana": "solana:mainnet",
    "tron": "tron:mainnet",
}

# Which assets each CAIP-2 network accepts — derived from the canonical table.
NETWORK_ASSETS: dict[str, list[str]] = {
    network: list(assets) for network, assets in SUPPORTED_ASSETS.items()
}

# Stablecoin USD-per-unit is 1:1; volatile assets use a static fallback quote
# (a live oracle would refine this; contract §4). Mirrors x402-py
# ``usd_per_unit_for``. None => asset cannot be quoted honestly -> skipped.
_STABLECOIN_ASSETS = {"USDC", "USDT", "DAI", "POL", "MATIC", "TRX"}
_STATIC_USD_PER_UNIT = {"ETH": 3000.0, "WETH": 3000.0, "ARB": 1.0, "SOL": 150.0}


def normalize_network_id(network: str) -> str:
    """Map a legacy network name to its CAIP-2 id (spec §3)."""
    return LEGACY_TO_CAIP2.get(network.strip().lower(), network.strip())


def _pay_to_address(caip2: str) -> str:
    """Resolve the configured receiving wallet for a CAIP-2 network.

    Single payTo authority: the service-layer ``X402_NETWORK_WALLETS`` map
    (env ``X402_WALLET_<NETWORK>`` per CAIP-2 id, fallback ``X402_WALLET``).
    """
    return (X402_NETWORK_WALLETS.get(caip2) or X402_WALLET or "").strip()


def _usd_per_unit_for(asset: str) -> float | None:
    """USD price of ONE whole unit of ``asset`` (None => not quotable)."""
    asset = asset.upper()
    if asset in _STABLECOIN_ASSETS:
        return 1.0
    return _STATIC_USD_PER_UNIT.get(asset)


def _operation_description(operation: str) -> str:
    """Human-readable description for an operation from the pricing table."""
    info = X402_PRICING.get(operation) or X402_PRICING.get("api_access") or {}
    return str(info.get("description") or operation or "api_access")


def _option(
    *,
    operation: str,
    resource: str,
    description: str,
    caip2: str,
    asset: str,
    entry: dict[str, Any],
) -> dict[str, Any] | None:
    """One ``accepts[]`` entry (exact scheme, atomic amount, CAIP-2 id).

    Carries the asset SYMBOL in ``asset`` and the contract address in
    ``extra.contract`` (fleet v1 convention; the official v2 spec uses the
    address directly — see ``models.SUPPORTED_ASSETS``). Returns None when the
    option cannot be quoted honestly (no USD-per-unit or no atomic conversion).
    """
    unit_usd = _usd_per_unit_for(asset)
    if unit_usd is None:
        return None
    price_usd = resolve_price_usd(operation, caip2, asset)
    if price_usd <= 0:
        return None
    try:
        amount_atomic = asset_to_atomic(price_usd, asset, caip2)
    except ValueError:
        # Not verifiable/quotable on this network — never advertise it.
        return None
    return {
        "scheme": "exact",
        "network": caip2,
        "maxAmountRequired": str(amount_atomic),
        "resource": resource,
        "description": description,
        "mimeType": "application/json",
        "payToAddress": _pay_to_address(caip2),
        "asset": asset.upper(),
        "extra": {
            "name": str(entry.get("name") or asset),
            "version": "2",
            "operation": operation,
            "caip2": caip2,
            "verification": str(entry.get("verification") or ""),
            "contract": str(entry.get("contract") or ""),
            "decimals": int(entry.get("decimals") or 6),
            "priceUsd": price_usd,
            "usdPerUnit": unit_usd,
            "usdPerUnitSource": (
                "stablecoin" if asset.upper() in _STABLECOIN_ASSETS else "static-default"
            ),
        },
    }


def build_multi_asset_accepts(
    operation: str,
    resource: str = "",
    description: str = "",
) -> list[dict[str, Any]]:
    """Multi-asset ``accepts[]`` covering every quotable (network, asset).

    Only networks with a configured receiving wallet are advertised, and only
    assets that can be quoted + verified honestly (mirrors x402-py
    ``build_multi_asset_accepts`` skip rules).
    """
    if not description:
        description = _operation_description(operation)
    accepts: list[dict[str, Any]] = []
    for caip2, assets in SUPPORTED_ASSETS.items():
        if not _pay_to_address(caip2):
            # Only advertise networks we have a receiving wallet for.
            continue
        for asset, entry in assets.items():
            option = _option(
                operation=operation,
                resource=resource,
                description=description,
                caip2=caip2,
                asset=asset,
                entry=entry,
            )
            if option is not None:
                accepts.append(option)
    if not accepts:
        base = normalize_network_id("base")
        fallback = _option(
            operation=operation,
            resource=resource,
            description=description,
            caip2=base,
            asset="USDC",
            entry=SUPPORTED_ASSETS[base]["USDC"],
        )
        if fallback is not None:
            accepts.append(fallback)
    return accepts


def build_payment_required(
    operation: str,
    resource: str = "",
    multi_asset: bool = True,
) -> dict[str, Any]:
    """Build the spec-compliant ``{x402Version:1, accepts:[...]}`` body.

    TODO(v2): additive — x402Version=2 restructures accepts entries
    (``amount``/``payTo``, top-level ResourceInfo); keep this v1 shape until
    the gateway migrates.
    """
    accepts = build_multi_asset_accepts(operation, resource=resource)
    body: dict[str, Any] = {"x402Version": X402_VERSION, "accepts": accepts}
    if multi_asset:
        # Additive multi-chain surface so agents see every network/asset
        # (canonical row shapes — models.NetworkInfo / models.SupportedAsset).
        body["networks"] = [dict(network) for network in SUPPORTED_NETWORKS]
        body["supported_assets"] = flatten_supported_assets()
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
        log.debug("x402_payment_required", extra={"path": path, "caller": caller_key})
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
            log.debug("x402_payment_required", extra={"handler": getattr(fn, "__name__", "")})
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
    "LEGACY_TO_CAIP2",
    "NETWORK_ASSETS",
    "PAYMENT_VALIDITY_SECONDS",
    "PaymentStore",
    "X402Middleware",
    "X402_VERSION",
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
    "resolve_price_usd",
    "set_default_store",
]

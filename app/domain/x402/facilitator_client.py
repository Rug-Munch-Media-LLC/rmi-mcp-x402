"""x402-coffer facilitator HTTP client — source of truth for payment checks.

The x402 MCP gateway delegates payment verification and settlement to the
x402-coffer facilitator over HTTP instead of self-verifying in-house. This
module is that thin, fail-closed client:

- ``verify(...)``   -> ``POST {base}/v1/x402/verify``   (payment proof check)
- ``settle(...)``   -> ``POST {base}/v1/x402/settle``   (settlement request)

Fail-closed contract: a payment-gated call is only ever allowed through when
coffer itself answers ``verified: true``. Any transport error, timeout, 5xx or
ambiguous response resolves to ``verified: false`` — an unreachable facilitator
REJECTS the call (the caller maps this to ``402 Payment Required``); it never
allows a payment through unverified.

The facilitator URL is the canonical ``X402_FACILITATOR_URL`` env var
(declared in :mod:`app.domain.x402.config` ``X402Settings``); its default is
the local x402-coffer deployment ``http://localhost:8670``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

log = logging.getLogger(__name__)

#: Canonical env var (single source of truth: app.domain.x402.config).
FACILITATOR_URL_ENV = "X402_FACILITATOR_URL"
#: Default coffer deployment the MCP gateway points at.
DEFAULT_FACILITATOR_URL = "http://localhost:8670"
#: Default per-request timeout (seconds); ``X402_TIMEOUT_SECONDS`` overrides.
DEFAULT_TIMEOUT_SECONDS = 15.0
#: Retries AFTER the initial attempt (initial + retries total requests).
DEFAULT_RETRIES = 2

__all__ = [
    "CofferFacilitatorClient",
    "DEFAULT_FACILITATOR_URL",
    "FACILITATOR_URL_ENV",
    "get_facilitator_client",
    "set_facilitator_client",
]


def _timeout_seconds() -> float:
    try:
        return float(os.getenv("X402_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SECONDS


class CofferFacilitatorClient:
    """Async HTTP client for the x402-coffer facilitator (fail-closed).

    ``verify`` / ``settle`` never raise: every failure is folded into a
    fail-closed result dict so callers cannot accidentally let a request
    through because the facilitator was down.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout: float | None = None,
        retries: int = DEFAULT_RETRIES,
    ) -> None:
        base = base_url or os.getenv(FACILITATOR_URL_ENV) or DEFAULT_FACILITATOR_URL
        self.base_url = base.rstrip("/")
        self.retries = max(0, int(retries))
        t = timeout if timeout is not None else _timeout_seconds()
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(t, connect=min(5.0, t), pool=min(5.0, t)),
        )

    # ── public surface ────────────────────────────────────────────────────

    async def verify(
        self,
        *,
        payment_id: str,
        tx_hash: str = "",
        network: str = "",
        asset: str = "",
        amount_usd: float = 0.0,
    ) -> dict[str, Any]:
        """Ask coffer to verify a payment proof (POST /v1/x402/verify).

        Returns coffer's verdict JSON; on ANY failure returns a fail-closed
        ``{"verified": False, "reason": ...}`` dict (never raises).
        """
        payload = {
            "payment_id": payment_id,
            "tx_hash": tx_hash,
            "network": network,
            "asset": asset,
            "amount_usd": amount_usd,
        }
        response = await self._post("/v1/x402/verify", payload)
        if response is None:
            return {
                "verified": False,
                "reason": "facilitator_unreachable",
                "facilitator": "x402-coffer",
                "network": network,
            }
        try:
            data = response.json()
        except ValueError:
            return {
                "verified": False,
                "reason": f"facilitator_error: non-JSON response (HTTP {response.status_code})",
                "facilitator": "x402-coffer",
                "network": network,
            }
        if not isinstance(data, dict):
            return {
                "verified": False,
                "reason": f"facilitator_error: unexpected response shape (HTTP {response.status_code})",
                "facilitator": "x402-coffer",
                "network": network,
            }
        # Coffer is the source of truth: only an explicit verified:true passes.
        data.setdefault("verified", False)
        data.setdefault("facilitator", "x402-coffer")
        if not data.get("verified"):
            data.setdefault("reason", f"facilitator_error: HTTP {response.status_code}")
        return data

    async def settle(
        self,
        *,
        payment_id: str,
        tx_hash: str = "",
        network: str = "",
        asset: str = "",
        amount_usd: float = 0.0,
    ) -> dict[str, Any]:
        """Ask coffer to settle a verified payment (POST /v1/x402/settle)."""
        payload = {
            "payment_id": payment_id,
            "tx_hash": tx_hash,
            "network": network,
            "asset": asset,
            "amount_usd": amount_usd,
        }
        response = await self._post("/v1/x402/settle", payload)
        if response is None:
            return {"settled": False, "success": False, "reason": "facilitator_unreachable"}
        try:
            data = response.json()
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("settled", data.get("success", False))
        data.setdefault("success", data.get("settled", False))
        data.setdefault("reason", f"facilitator_error: HTTP {response.status_code}")
        return data

    async def aclose(self) -> None:
        await self._client.aclose()

    # ── internals ──────────────────────────────────────────────────────────

    async def _post(self, path: str, payload: dict[str, Any]) -> httpx.Response | None:
        """POST with bounded retries. Returns the response, or ``None`` when the
        facilitator could not be reached / kept erroring (fail closed)."""
        last_error = "unknown error"
        for attempt in range(self.retries + 1):
            try:
                response = await self._client.post(path, json=payload)
                if response.status_code < 500:
                    return response  # 2xx/3xx/4xx are definitive verdicts
                last_error = f"HTTP {response.status_code}"
            except httpx.TimeoutException as exc:
                last_error = f"timeout: {exc.__class__.__name__}"
            except httpx.TransportError as exc:
                last_error = f"transport: {exc.__class__.__name__}"
            log.warning(
                "x402_coffer_facilitator_attempt_failed",
                extra={"path": path, "attempt": attempt + 1, "error": last_error},
            )
            if attempt < self.retries:
                await asyncio.sleep(0.25 * (2**attempt))
        log.error(
            "x402_coffer_facilitator_unreachable",
            extra={"path": path, "error": last_error},
        )
        return None


# ── Process-wide client (mirrors get_default_store / get_facilitator_router) ─

_facilitator_client: CofferFacilitatorClient | None = None


def get_facilitator_client() -> CofferFacilitatorClient:
    """Return (creating once) the process-wide coffer facilitator client."""
    global _facilitator_client
    if _facilitator_client is None:
        _facilitator_client = CofferFacilitatorClient()
    return _facilitator_client


def set_facilitator_client(client: CofferFacilitatorClient | None) -> None:
    """Override (or clear, with ``None``) the process-wide client."""
    global _facilitator_client
    _facilitator_client = client

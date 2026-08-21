"""Shared harness for the x402 spec-conformance suite.

Builds a spec-compliant FastAPI app (the conformance target) around the x402
spec interface. The tests assert the OFFICIAL x402 HTTP contract — not any
particular production bug — so this harness wires a minimal conformant
server and injects a StubFacilitator to exercise the verify/underpayment/
nonce/replay paths deterministically without touching a network.

Backend resolution: the repo's own ``app.domain.x402`` is expected to become
spec-conformant (spec contract G10 says vendor/delegate to x402-py). Until
then (and always in CI), we bind the spec interface to the authoritative
``x402`` package. Tests raise ``SKIP`` only if the x402 spec entrypoints are
completely absent.
"""

from __future__ import annotations

import importlib
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Callable
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# ── Spec backend resolution ─────────────────────────────────────────────────
# The "x402" import name collides in this environment: the pip-installed
# ``x402`` (v2.x SDK) shadows the authoritative reference implementation at
# ``x402-py`` (which owns x402.scheme / x402.handler / x402.facilitator and is
# the SOURCE OF TRUTH for this suite per X402-SPEC-CONTRACT.md §G10). Robusto
# order:
#   1. the repo's own spec-conformant surface (app.domain.x402.*) once agents
#      land it (G10 delegates to x402-py).
#   2. a reference ``x402-py`` on the path (installed or via X402_SPEC_PATH).
#   3. skip-if-absent otherwise.

X402_REFERENCE_CANDIDATES = [
    os.environ.get("X402_SPEC_PATH", ""),
    "/srv/work/repos/x402-py",  # local reference checkout on this fleet
]


def _has_module(name: str) -> bool:
    try:
        importlib.import_module(name)
        return True
    except Exception:
        return False


def _resolve_spec_module() -> str:
    """Return the import path that exposes the spec symbols, or raise ImportError."""
    # 1. Repo-native surface (agents will add app.domain.x402.handler/scheme).
    if (
        _has_module("app")
        and _has_module("app.domain")
        and _has_module("app.domain.x402")
        and _has_module("app.domain.x402.handler")
        and _has_module("app.domain.x402.scheme")
    ):
        return "app.domain.x402"
    # 2. A reference x402-py that owns the spec modules (the v2 SDK does NOT).
    for ref in X402_REFERENCE_CANDIDATES:
        if not ref:
            continue
        if os.path.isdir(ref) and os.path.isfile(os.path.join(ref, "x402", "handler.py")):
            if ref not in sys.path:
                sys.path.insert(0, ref)
            if _has_module("x402.handler") and _has_module("x402.scheme"):
                # Ensure we keep the reference ahead of the pip SDK on later hits.
                return "x402"
    if _has_module("x402.handler") and _has_module("x402.scheme"):
        return "x402"
    raise ModuleNotFoundError("no x402 spec implementation available")


_SPEC_MOD = _resolve_spec_module()

if _SPEC_MOD == "app.domain.x402":
    from app.domain.x402.handler import X402Handler  # type: ignore[no-redef]
    from app.domain.x402.scheme import (  # type: ignore[no-redef]
        VERIFICATION_STATUS,
        asset_to_atomic,
        network_family,
        network_verification_status,
        normalize_network_id,
    )
else:
    pytest.importorskip("x402.handler", reason="x402 spec package not installed")
    from x402.handler import X402Handler
    from x402.scheme import (
        VERIFICATION_STATUS,
        asset_to_atomic,
        network_family,
        network_verification_status,
        normalize_network_id,
    )

# Server-authoritative price for the operation under test (atomic quoted off
# this, so underpayment is detected the same way a real verifier would).
SERVICE_OPERATION = "scrape"
SERVICE_PRICE_USD = 0.001  # matches x402.GetPricing["scrape"] price_usd


class StubFacilitator:
    """Deterministic stand-in for the facilitator.verify contract.

    - TRON network is SCAFFOLDED: never returns ``verified:true``.
    - nonce reuse (same tx_hash twice) → rejected.
    - underpayment (paid_atomic < server-required atomic) → rejected.
    - overpayment → accepted.
    """

    def __init__(self, nonce_store: set[str]) -> None:
        self.nonce_store = nonce_store
        # tx_hash -> claimed {"network","asset","amount_atomic"} recorded by
        # the pay endpoint before calling verify.
        self.latest: dict[str, dict[str, Any]] = {}

    async def verify(
        self,
        payment_id: str,
        tx_hash: str,
        network: str,
        asset: str,
        amount_usd: float,
    ) -> dict[str, Any]:
        provenance = self.latest.get(tx_hash, {})
        paid_atomic = int(provenance.get("amount_atomic") or 0)
        required_atomic = asset_to_atomic(amount_usd, asset, network)

        if network_family(network) == "tron":
            return {
                "verified": False,
                "reason": "needs_facilitator:scaffold",
                "settlement": None,
                "facilitator": "scaffold_tron",
            }
        if tx_hash in self.nonce_store:
            return {
                "verified": False,
                "reason": "nonce_already_used",
                "settlement": None,
                "facilitator": "stub",
            }
        if paid_atomic < required_atomic:
            return {
                "verified": False,
                "reason": "insufficient_funds",
                "settlement": None,
                "facilitator": "stub",
            }
        # Nonce burn: only the first use of a tx_hash succeeds.
        self.nonce_store.add(tx_hash)
        return {"verified": True, "reason": "", "settlement": None, "facilitator": "stub"}

    async def settle(self, *_: Any, **__: Any) -> dict[str, Any]:
        return {"success": True, "facilitator": "stub"}


@dataclass
class X402Harness:
    """A fresh conformant server instance with full state introspection."""

    app: FastAPI
    handler: X402Handler
    facilitator: StubFacilitator
    nonce_store: set[str]

    def _client_for(self, ip: str) -> httpx.AsyncClient:
        async def _scoped(scope: dict[str, Any], receive: Any, send: Any) -> None:
            if scope.get("type") == "http":
                scope["client"] = (ip, 9999)  # caller identity lives on the IP
            await self.app(scope, receive, send)

        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_scoped), base_url="http://testserver"
        )

    def caller(self, ip: str) -> httpx.AsyncClient:
        """AsyncClient whose requests are attributed to ``ip``."""
        return self._client_for(ip)

    def inject_expired_payment(self, payment_id: str, caller_key: str = "1.1.1.1") -> None:
        """Plant a verified-but-expired payment into the handler's record."""
        self.handler.payments.append(
            {
                "payment_id": payment_id,
                "tx_hash": "0xexpired",
                "network": "eip155:8453",
                "asset": "USDC",
                "amount_usd": SERVICE_PRICE_USD,
                "verified": True,
                "reason": "",
                "facilitator": "stub",
                "caller_key": caller_key,
                "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
            }
        )


@pytest.fixture
def x402() -> X402Harness:
    """Build a fresh conformant server per test (isolated state)."""
    nonce_store: set[str] = set()
    facilitator = StubFacilitator(nonce_store)
    handler = X402Handler()
    handler.router = facilitator  # type: ignore[assignment]  # duck-typed FacRouter
    harness = X402Harness(
        app=FastAPI(), handler=handler, facilitator=facilitator, nonce_store=nonce_store
    )
    app = harness.app

    @app.get("/v1/x402/health")
    async def health() -> dict[str, Any]:
        """Honest verification status — scaffolded chains never reported live."""
        return {
            "verification_status": VERIFICATION_STATUS,
            "verification_status_by_network": {
                network_id: network_verification_status(network_id)
                for network_id in VERIFICATION_STATUS
            },
        }

    @app.post("/v1/x402/pay")
    async def pay(body: dict[str, Any], request: Request) -> JSONResponse:
        tx_hash = str(body["tx_hash"])
        network = normalize_network_id(str(body["network"]))
        asset = str(body["asset"]).upper()
        # Server records what the client CLAIMS to have paid; verify() then
        # cross-checks it against server-side pricing (atomic comparison).
        facilitator.latest[tx_hash] = {
            "network": network,
            "asset": asset,
            "amount_atomic": str(body.get("amount_atomic", 0)),
        }
        payment_id = str(body.get("payment_id", uuid4().hex))
        caller_key = request.client.host if request.client else ""
        result = await handler.verify_payment(
            payment_id,
            tx_hash,
            network=network,
            asset=asset,
            operation=SERVICE_OPERATION,
            caller_key=caller_key,
        )
        if not result["verified"]:
            return JSONResponse(
                content={"verified": False, "reason": result["reason"]}, status_code=402
            )
        return JSONResponse(
            content={
                "verified": True,
                "payment_id": payment_id,
                "reason": result.get("reason", ""),
                "facilitator": result.get("facilitator", ""),
            },
        )

    @app.get("/v1/x402/paid")
    async def paid(request: Request) -> JSONResponse:
        payment_id = request.headers.get("X-Payment-ID", "")
        caller_key = request.client.host if request.client else ""
        if payment_id and handler.is_paid(payment_id, caller_key):
            return JSONResponse(content={"data": "ok"})
        body, headers = handler.create_payment_required(
            SERVICE_OPERATION, resource=str(request.url), multi_asset=True
        )
        return JSONResponse(content=body, status_code=402, headers=headers)

    return harness


@pytest.fixture
def paid_client(x402: X402Harness) -> Callable[[str | None], httpx.AsyncClient]:
    """Factory returning an AsyncClient for a caller (default 1.1.1.1)."""

    def _make(ip: str | None = None) -> httpx.AsyncClient:
        return x402.caller(ip or "1.1.1.1")

    return _make

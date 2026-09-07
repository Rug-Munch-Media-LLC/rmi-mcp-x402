"""Payment-gate hardening for the x402 MCP HTTP server (app.mcp.server_app).

Regression tests for the 2026-09-06 cross-cutting-security audit finding:
the premium-bundle gate in ``X402MCPServer`` previously unlocked on the
PRESENCE of an ``X-Payment`` / ``X-Solana-Payment`` header — any string
passed. The gate now requires a payment id VERIFIED by the
facilitator/coffer (recorded caller-bound + TTL'd in the shared
``PaymentStore`` after ``POST /v1/x402/verify``) replayed on
``X-Payment-ID``.

These tests run the REAL ``X402MCPServer.streamable_http_app()`` over
``httpx.ASGITransport`` — the only stand-in is the caller IP, which is where
the store binds payments.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.domain.x402.middleware import get_default_store, set_default_store
from app.mcp.server_app import X402MCPServer

CALLER_IP = "5.6.7.8"
# A premium bundle id (price_usd > 0, requires_payment=True) from the real
# tool catalog — exercises the middleware's paid-bundle gate.
PREMIUM_PATH = "/token-pulse/risk/0xabc"


def _client_for(app: Any, ip: str = CALLER_IP) -> httpx.AsyncClient:
    """AsyncClient whose requests carry a fixed caller IP (store binding)."""

    async def _scoped(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") == "http":
            scope["client"] = (ip, 9999)
        await app(scope, receive, send)

    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_scoped), base_url="http://testserver"
    )


@pytest.fixture(autouse=True)
def _fresh_gate_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fresh app + payment store per test; API-key auth disabled."""
    monkeypatch.setenv("RMI_MCP_API_KEY", "")
    set_default_store(None)
    yield
    set_default_store(None)


async def test_paid_bundle_without_verified_payment_returns_402() -> None:
    app = X402MCPServer().streamable_http_app()
    async with _client_for(app) as client:
        r = await client.get(PREMIUM_PATH)
    assert r.status_code == 402
    assert r.json().get("code") == "PAYMENT_REQUIRED"


async def test_legacy_presence_headers_no_longer_unlock() -> None:
    """X-Payment / X-Solana-Payment with ANY value must NOT unlock the gate.

    Pre-audit, an arbitrary string in either header skipped the 402 entirely;
    a non-existent route then produced 404. We assert 402 to prove the
    presence-only gate is gone.
    """
    app = X402MCPServer().streamable_http_app()
    async with _client_for(app) as client:
        for header in ("X-Payment", "X-Solana-Payment"):
            r = await client.get(PREMIUM_PATH, headers={header: "any-string-would-do"})
            assert r.status_code == 402, f"{header} must not unlock (got {r.status_code})"
            assert r.json().get("code") == "PAYMENT_REQUIRED"


async def test_unverified_payment_id_rejected() -> None:
    """An X-Payment-ID that was never confirmed by the facilitator/coffer is
    rejected even when the caller knows the id (fail-closed, no fabrication)."""
    app = X402MCPServer().streamable_http_app()
    async with _client_for(app) as client:
        r = await client.get(PREMIUM_PATH, headers={"X-Payment-ID": "pay_made_up"})
    assert r.status_code == 402


async def test_verified_payment_id_unlocks_for_bound_caller() -> None:
    """A payment id VERIFIED via the facilitator/coffer (recorded in the shared
    PaymentStore for this caller) passes the gate; replaying it passes."""
    store = get_default_store()
    store.record(
        payment_id="pay_verified_0001",
        caller_key=CALLER_IP,
        tx_hash="0xconfirmed",
        network="eip155:8453",
        asset="USDC",
        amount_usd=0.01,
        verified=True,
    )
    app = X402MCPServer().streamable_http_app()
    async with _client_for(app) as client:
        r = await client.get(PREMIUM_PATH, headers={"X-Payment-ID": "pay_verified_0001"})
    # Gate passed (no 402); the premium tool route is not mounted here, so the
    # app answers 404 — the assertion is that payment no longer blocks it.
    assert r.status_code != 402


async def test_verified_payment_id_rejected_for_other_caller() -> None:
    """The verified id is caller-bound: the same id from a different IP still
    gets 402 (replay across callers is not allowed)."""
    store = get_default_store()
    store.record(
        payment_id="pay_caller_bound_0001",
        caller_key=CALLER_IP,
        tx_hash="0xconfirmed2",
        network="eip155:8453",
        asset="USDC",
        amount_usd=0.01,
        verified=True,
    )
    app = X402MCPServer().streamable_http_app()
    async with _client_for(app, ip="9.9.9.9") as client:
        r = await client.get(PREMIUM_PATH, headers={"X-Payment-ID": "pay_caller_bound_0001"})
    assert r.status_code == 402

"""Signed-admin-token auth for the Verified Badge admin endpoints.

Regression tests for the 2026-09-06 service-health audit finding: the
``/api/v1/verified/admin/revoke/...`` and ``/admin/renew/...`` routes had NO
auth — anonymous callers could revoke/renew a token's badge. Every request
must now carry an HMAC-SHA256 signature over
``"{method} {path} {timestamp}"`` (shared ``VERIFIED_ADMIN_SECRET``) plus a
fresh ``X-Admin-Timestamp``; anonymous, forged, stale and cross-route
signatures are rejected with 401.
"""

from __future__ import annotations

import time

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.verified.security import sign_admin_request

SECRET = "test-admin-secret-not-for-prod"
REVOKE_URL = "/api/v1/verified/admin/revoke/base/0xTokenAddr?reason=rug&evidence=audit"
RENEW_URL = "/api/v1/verified/admin/renew/base/0xTokenAddr"


def _app() -> FastAPI:
    from app.api.v1.verified.router import router as verified_router

    app = FastAPI()
    app.include_router(verified_router)
    return app


def _signed_headers(
    method: str, path: str, *, secret: str = SECRET, timestamp: int | None = None
) -> dict[str, str]:
    ts = str(timestamp if timestamp is not None else int(time.time()))
    return {
        "X-Admin-Timestamp": ts,
        "X-Admin-Signature": sign_admin_request(secret, method, path, ts),
    }


def _path_only(url: str) -> str:
    """Strip the query string: signatures cover ``method path timestamp``."""
    return url.split("?", 1)[0]


@pytest.fixture(autouse=True)
def _admin_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VERIFIED_ADMIN_SECRET", SECRET)


@pytest.fixture
async def client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=_app())
    c = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    yield c
    await c.aclose()


async def test_admin_revoke_rejects_anonymous(client: httpx.AsyncClient) -> None:
    r = await client.post(REVOKE_URL)
    assert r.status_code == 401


async def test_admin_renew_rejects_anonymous(client: httpx.AsyncClient) -> None:
    r = await client.post(RENEW_URL)
    assert r.status_code == 401


async def test_admin_revoke_rejects_forged_signature(client: httpx.AsyncClient) -> None:
    headers = _signed_headers("POST", _path_only(REVOKE_URL))
    headers["X-Admin-Signature"] = "0" * 64  # forged
    r = await client.post(REVOKE_URL, headers=headers)
    assert r.status_code == 401


async def test_admin_revoke_rejects_stale_timestamp(client: httpx.AsyncClient) -> None:
    stale = int(time.time()) - 3600  # beyond MAX_SKEW_SECONDS (300s)
    headers = _signed_headers("POST", _path_only(REVOKE_URL), timestamp=stale)
    r = await client.post(REVOKE_URL, headers=headers)
    assert r.status_code == 401


async def test_admin_revoke_rejects_cross_endpoint_replay(client: httpx.AsyncClient) -> None:
    """A valid revoke signature must not authorise renew (path is signed)."""
    headers = _signed_headers("POST", _path_only(REVOKE_URL))
    r = await client.post(RENEW_URL, headers=headers)  # same sig, different path
    assert r.status_code == 401


async def test_admin_revoke_accepts_valid_signature(client: httpx.AsyncClient) -> None:
    headers = _signed_headers("POST", _path_only(REVOKE_URL))
    r = await client.post(REVOKE_URL, headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == "revoked"


async def test_admin_renew_accepts_valid_signature(client: httpx.AsyncClient) -> None:
    headers = _signed_headers("POST", _path_only(RENEW_URL))
    r = await client.post(RENEW_URL, headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == "renewed"


async def test_admin_routes_fail_closed_without_configured_secret(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unset VERIFIED_ADMIN_SECRET -> the admin surface refuses to operate."""
    monkeypatch.delenv("VERIFIED_ADMIN_SECRET", raising=False)
    headers = _signed_headers("POST", _path_only(REVOKE_URL))
    r = await client.post(REVOKE_URL, headers=headers)
    assert r.status_code == 503

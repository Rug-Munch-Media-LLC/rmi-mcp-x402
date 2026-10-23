"""AI Guard fail-closed hardening (app.mcp.ai_guard + server middleware).

Regression tests for the 2026-10-23 fail-open security-control bug: when a
POST/PUT/PATCH body could not be read, ``check_request`` silently passed the
request through — an attacker could force a body-read failure to bypass the
malicious-pattern scan. The guard now FAILS CLOSED with a structured,
non-leaking reason code; the 402 payment flow stays untouched.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from starlette.requests import Request

from app.mcp.ai_guard import (
    REASON_BODY_NOT_UTF8,
    REASON_BODY_UNREADABLE,
    REASON_PATTERN,
    REASON_SCANNER,
    AIGuardWrapper,
)
from app.mcp.server_app import X402MCPServer

PREMIUM_PATH = "/token-pulse/risk/0xabc"
CALLER_IP = "5.6.7.8"


def _request(
    method: str = "POST",
    receive: Any = None,
    body: bytes | None = b"{}",
    user_agent: str = "munch-client/1.0",
) -> Request:
    """Raw starlette Request over an explicit scope + receive, so tests can
    inject a failing body stream or a malformed (non-UTF-8) body."""
    headers = [(b"user-agent", user_agent.encode())]
    scope: dict[str, Any] = {
        "type": "http",
        "method": method,
        "path": PREMIUM_PATH,
        "headers": headers,
    }
    if receive is None:
        messages: list[dict[str, Any]] = []
        if body is not None:
            messages.append({"type": "http.request", "body": body, "more_body": False})
        messages.append({"type": "http.disconnect"})

        async def _receive() -> dict[str, Any]:
            return messages.pop(0) if len(messages) > 1 else {"type": "http.disconnect"}

        receive = _receive
    return Request(scope, receive)


# ── unit: AIGuardWrapper.check_request ───────────────────────────────────────


@pytest.mark.asyncio
async def test_body_read_failure_fails_closed() -> None:
    """Unreadable body is a violation (fail closed), never a silent bypass."""

    async def boom() -> dict[str, Any]:
        raise RuntimeError("boom")

    verdict = await AIGuardWrapper().check_request(_request(receive=boom))
    assert verdict == (False, REASON_BODY_UNREADABLE)


@pytest.mark.asyncio
async def test_body_read_failure_does_not_leak_internals() -> None:
    """The reason carries NO exception class/message from the read failure."""

    async def boom() -> dict[str, Any]:
        raise PermissionError("secret-path /var/lib/vault denied")

    reason = (await AIGuardWrapper().check_request(_request(receive=boom)))[1]
    assert reason is not None
    assert "secret-path" not in reason
    assert "PermissionError" not in reason
    assert reason == REASON_BODY_UNREADABLE


@pytest.mark.asyncio
async def test_malformed_body_fails_closed() -> None:
    """Non-UTF-8 bodies are blocked instead of decoded with errors=ignore."""
    verdict = await AIGuardWrapper().check_request(_request(body=b"token=1; drop \xff\xfe"))
    assert verdict == (False, REASON_BODY_NOT_UTF8)


@pytest.mark.asyncio
async def test_valid_clean_body_passes() -> None:
    request = _request(body=json.dumps({"x402Version": 1, "accepted": {}}).encode())
    verdict = await AIGuardWrapper().check_request(request)
    assert verdict == (True, None)


@pytest.mark.asyncio
async def test_malicious_pattern_body_blocked() -> None:
    """Bypass attempt: pattern is hidden in a valid UTF-8 JSON body."""
    request = _request(body=b'{"q": "1 UNION SELECT password FROM users"}')
    verdict = await AIGuardWrapper().check_request(request)
    assert verdict == (False, REASON_PATTERN)


@pytest.mark.asyncio
async def test_case_normalized_bypass_attempt_blocked() -> None:
    """Bypass attempt: uppercase pattern ("SELECT * FROM") hidden in body —
    the scan is case-normalized on both sides (pre 2026-10-23, the lowered
    body could never match upper-case pattern entries)."""
    request = _request(body=b'{"q": "SELECT * FROM users -""}')
    verdict = await AIGuardWrapper().check_request(request)
    assert verdict == (False, REASON_PATTERN)


@pytest.mark.asyncio
async def test_scanner_user_agent_blocked() -> None:
    request = _request(method="GET", body=None, user_agent="sqlmap/1.8")
    verdict = await AIGuardWrapper().check_request(request)
    assert verdict == (False, REASON_SCANNER)


@pytest.mark.asyncio
async def test_get_requests_still_pass_clean() -> None:
    verdict = await AIGuardWrapper().check_request(_request(method="GET", body=None))
    assert verdict == (True, None)


# ── integration: server middleware + 402 preservation ────────────────────────


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
    monkeypatch.setenv("RMI_MCP_API_KEY", "")
    monkeypatch.setenv("X402_FACILITATOR_URL", "http://coffer.test")


@pytest.mark.asyncio
async def test_guard_error_is_structured_and_does_not_leak() -> None:
    """Malicious body through the real app -> 403 with an AI_GUARD_* code."""
    from app.domain.x402.middleware import set_default_store

    set_default_store(None)
    app = X402MCPServer().streamable_http_app()
    async with _client_for(app) as client:
        resp = await client.post(PREMIUM_PATH, content=b'{"q": "SELECT * FROM"}', headers={})
    assert resp.status_code == 403
    payload = resp.json()
    assert payload["code"] == REASON_PATTERN


@pytest.mark.asyncio
async def test_clean_body_still_reaches_402_with_options() -> None:
    """FAIL CLOSED must not break the x402 offer surface: a clean unpaid
    premium POST still gets the 402 with its payment options intact."""
    from app.domain.x402.middleware import set_default_store

    set_default_store(None)
    app = X402MCPServer().streamable_http_app()
    async with _client_for(app) as client:
        resp = await client.post(PREMIUM_PATH, content=b"{}")
    assert resp.status_code == 402
    payload = resp.json()
    assert payload["code"] == "PAYMENT_REQUIRED"
    assert resp.json()["payment_options"]["base"]["amount_usd"] > 0

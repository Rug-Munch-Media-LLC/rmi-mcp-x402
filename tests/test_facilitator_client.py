"""CofferFacilitatorClient tests — respx-mocked x402-coffer HTTP.

The client is the MCP gateway's payment-verification path to x402-coffer.
Contract under test:

- ``verify`` POSTs to ``/v1/x402/verify`` and returns coffer's verdict;
- the client FAILS CLOSED: transport errors, timeouts and 5xx (after bounded
  retries) resolve to ``verified: false`` — never an exception, never a pass;
- ``settle`` POSTs to ``/v1/x402/settle`` and fails closed the same way.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.domain.x402.facilitator_client import (
    DEFAULT_FACILITATOR_URL,
    CofferFacilitatorClient,
    get_facilitator_client,
    set_facilitator_client,
)

VERIFY_URL = f"{DEFAULT_FACILITATOR_URL}/v1/x402/verify"
SETTLE_URL = f"{DEFAULT_FACILITATOR_URL}/v1/x402/settle"


@pytest.fixture(autouse=True)
def _no_singleton_leak():
    set_facilitator_client(None)
    yield
    set_facilitator_client(None)


def _client() -> CofferFacilitatorClient:
    return CofferFacilitatorClient(retries=1)


@respx.mock
@pytest.mark.asyncio
async def test_verify_passes_coffer_verdict_through() -> None:
    respx.post(VERIFY_URL).mock(
        return_value=httpx.Response(
            200, json={"verified": True, "payment_id": "pay_1", "reason": ""}
        )
    )
    client = _client()
    try:
        result = await client.verify(
            payment_id="pay_1",
            tx_hash="0xabc",
            network="eip155:8453",
            asset="USDC",
            amount_usd=0.01,
        )
    finally:
        await client.aclose()
    assert result["verified"] is True
    assert result["payment_id"] == "pay_1"
    assert result.get("facilitator") == "x402-coffer"
    route = respx.calls.last.request
    assert route.url.path == "/v1/x402/verify"
    assert route.content is not None


@respx.mock
@pytest.mark.asyncio
async def test_verify_reports_refusal_not_verified() -> None:
    respx.post(VERIFY_URL).mock(
        return_value=httpx.Response(
            200, json={"verified": False, "reason": "underpayment", "payment_id": "pay_1"}
        )
    )
    client = _client()
    try:
        result = await client.verify(
            payment_id="pay_1",
            tx_hash="0xabc",
            network="eip155:8453",
            asset="USDC",
            amount_usd=0.01,
        )
    finally:
        await client.aclose()
    assert result["verified"] is False
    assert "underpayment" in result.get("reason", "")


@respx.mock
@pytest.mark.asyncio
async def test_verify_4xx_is_definitive_not_retried() -> None:
    route = respx.post(VERIFY_URL).mock(return_value=httpx.Response(400, json={"error": "bad"}))
    client = _client()
    try:
        result = await client.verify(
            payment_id="pay_1",
            tx_hash="0xabc",
            network="eip155:8453",
            asset="USDC",
            amount_usd=0.01,
        )
    finally:
        await client.aclose()
    assert result["verified"] is False
    assert len(route.calls) == 1  # 4xx is a definitive verdict — no retry


@respx.mock
@pytest.mark.asyncio
async def test_verify_fails_closed_when_coffer_unreachable() -> None:
    # Transport error (connection refused / DNS) -> fail closed, never raise.
    respx.post(VERIFY_URL).mock(side_effect=httpx.ConnectError("coffer down"))
    client = _client()
    try:
        result = await client.verify(
            payment_id="pay_1",
            tx_hash="0xabc",
            network="eip155:8453",
            asset="USDC",
            amount_usd=0.01,
        )
    finally:
        await client.aclose()
    assert result["verified"] is False
    assert "facilitator_unreachable" in result.get("reason", "")


@respx.mock
@pytest.mark.asyncio
async def test_verify_fails_closed_on_5xx_after_retries() -> None:
    route = respx.post(VERIFY_URL).mock(return_value=httpx.Response(503, json={}))
    client = CofferFacilitatorClient(retries=2)
    try:
        result = await client.verify(
            payment_id="pay_1",
            tx_hash="0xabc",
            network="eip155:8453",
            asset="USDC",
            amount_usd=0.01,
        )
    finally:
        await client.aclose()
    assert result["verified"] is False
    assert "facilitator_unreachable" in result.get("reason", "")
    assert len(route.calls) == 3  # initial + 2 retries


@respx.mock
@pytest.mark.asyncio
async def test_verify_recovers_after_transient_5xx() -> None:
    route = respx.post(VERIFY_URL).mock(
        side_effect=[
            httpx.Response(500, json={}),
            httpx.Response(200, json={"verified": True, "payment_id": "pay_1"}),
        ]
    )
    client = _client()
    try:
        result = await client.verify(
            payment_id="pay_1",
            tx_hash="0xabc",
            network="eip155:8453",
            asset="USDC",
            amount_usd=0.01,
        )
    finally:
        await client.aclose()
    assert result["verified"] is True
    assert len(route.calls) == 2


@respx.mock
@pytest.mark.asyncio
async def test_verify_non_json_response_fails_closed() -> None:
    respx.post(VERIFY_URL).mock(return_value=httpx.Response(200, text="<html>not json</html>"))
    client = _client()
    try:
        result = await client.verify(
            payment_id="pay_1",
            tx_hash="0xabc",
            network="eip155:8453",
            asset="USDC",
            amount_usd=0.01,
        )
    finally:
        await client.aclose()
    assert result["verified"] is False


@respx.mock
@pytest.mark.asyncio
async def test_settle_posts_and_fails_closed() -> None:
    respx.post(SETTLE_URL).mock(
        return_value=httpx.Response(200, json={"settled": True, "transaction": "0xsettled"})
    )
    client = _client()
    try:
        ok = await client.settle(
            payment_id="pay_1",
            tx_hash="0xabc",
            network="eip155:8453",
            asset="USDC",
            amount_usd=0.01,
        )
    finally:
        await client.aclose()
    assert ok["settled"] is True

    # Unreachable coffer -> settlement fails closed (never raises).
    respx.reset()
    respx.post(SETTLE_URL).mock(side_effect=httpx.ConnectError("coffer down"))
    client = _client()
    try:
        down = await client.settle(
            payment_id="pay_1",
            tx_hash="0xabc",
            network="eip155:8453",
            asset="USDC",
            amount_usd=0.01,
        )
    finally:
        await client.aclose()
    assert down["settled"] is False
    assert "facilitator_unreachable" in down.get("reason", "")


@respx.mock
@pytest.mark.asyncio
async def test_singleton_rebuilds_from_env() -> None:
    set_facilitator_client(None)
    client = get_facilitator_client()
    try:
        assert client.base_url == DEFAULT_FACILITATOR_URL
    finally:
        await client.aclose()
        set_facilitator_client(None)

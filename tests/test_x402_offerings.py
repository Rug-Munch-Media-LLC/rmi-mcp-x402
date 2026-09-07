"""Paid-offering endpoint tests — respx-mocked x402-coffer facilitator.

Contract under test (2026-09-07 selling strategy, offerings 1-3):

- ``POST /v1/verdict/{token}``, ``GET /v1/dossier/{wallet}``,
  ``GET /v1/cluster/{wallet}`` are FAIL-CLOSED x402-gated:
  * no / unverified ``X-Payment-ID``            -> 402 (never 200);
  * coffer answers ``verified: false``          -> 402;
  * coffer unreachable / 5xx / non-JSON         -> 402 (fail closed);
  * coffer answers ``verified: true``           -> 200 with the offering's
    evidence shape (named factors / dossier fields / 4 heuristics).
- The verdict ABSTAINS honestly ("insufficient data") when fewer than
  ``MIN_KNOWN_FACTORS`` named factors are known — asserted structurally,
  not tautologically.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.domain.x402.facilitator_client import (
    DEFAULT_FACILITATOR_URL,
    set_facilitator_client,
)
from app.domain.x402.offerings import CLUSTER_HEURISTICS, MIN_KNOWN_FACTORS, VERDICT_FACTORS
from app.domain.x402.offerings_router import router
from app.domain.x402.store import PaymentStore, set_default_store

VERIFY_URL = f"{DEFAULT_FACILITATOR_URL}/v1/x402/verify"
CALLER = "198.51.100.7"  # TEST-NET-3 documentation IP
PAYMENT_ID = "pay_offerings_1"


@pytest.fixture(autouse=True)
def _isolated_payment_state():
    set_facilitator_client(None)
    store = PaymentStore(validity_seconds=3600)
    set_default_store(store)
    yield
    set_default_store(None)
    set_facilitator_client(None)


def _record_verified_payment() -> None:
    from app.domain.x402.store import get_default_store

    get_default_store().record(
        payment_id=PAYMENT_ID,
        caller_key=CALLER,
        tx_hash="0xabc",
        network="eip155:8453",
        asset="USDC",
        amount_usd=0.05,
        facilitator="x402-coffer",
    )


def _client() -> AsyncClient:
    app = FastAPI()
    app.include_router(router)
    transport = ASGITransport(app=app, client=(CALLER, 12345))
    return AsyncClient(transport=transport, base_url="http://test")


@respx.mock
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/v1/verdict/So11111111111111111111111111111111111111112"),
        ("get", "/v1/dossier/9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"),
        ("get", "/v1/cluster/9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"),
    ],
)
async def test_unverified_payment_is_402_fail_closed(method: str, path: str) -> None:
    """No payment id at all -> 402 with the x402 offer headers."""
    respx.post(VERIFY_URL).mock(return_value=httpx.Response(200, json={"verified": True}))
    async with _client() as ac:
        response = await ac.request(method, path)
    assert response.status_code == 402
    assert response.headers.get("x-payment-required") == "true"
    assert response.headers.get("x-payment-options") == "x402"
    body = response.json()
    assert body["detail"]["code"] == "PAYMENT_REQUIRED"


@respx.mock
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/v1/verdict/So11111111111111111111111111111111111111112"),
        ("get", "/v1/dossier/9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"),
        ("get", "/v1/cluster/9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"),
    ],
)
async def test_coffer_refusal_is_402(method: str, path: str) -> None:
    """Recorded id but coffer says NOT verified -> 402, handler never runs."""
    _record_verified_payment()
    respx.post(VERIFY_URL).mock(
        return_value=httpx.Response(200, json={"verified": False, "reason": "replay_detected"})
    )
    async with _client() as ac:
        response = await ac.request(method, path, headers={"X-Payment-ID": PAYMENT_ID})
    assert response.status_code == 402
    assert "replay" in response.json()["detail"]["reason"]


@respx.mock
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/v1/verdict/So11111111111111111111111111111111111111112"),
        ("get", "/v1/dossier/9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"),
        ("get", "/v1/cluster/9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"),
    ],
)
async def test_coffer_unreachable_is_402_fail_closed(method: str, path: str) -> None:
    """Facilitator transport error -> 402. Never allow-through unverified."""
    _record_verified_payment()
    respx.post(VERIFY_URL).mock(side_effect=httpx.ConnectError("coffer down"))
    async with _client() as ac:
        response = await ac.request(method, path, headers={"X-Payment-ID": PAYMENT_ID})
    assert response.status_code == 402
    assert response.json()["detail"]["reason"] == "facilitator_unreachable"


@respx.mock
@pytest.mark.asyncio
async def test_verdict_abstains_without_enough_known_factors() -> None:
    """With stub sources (< MIN_KNOWN_FACTORS known) the verdict ABSTAINS."""
    _record_verified_payment()
    respx.post(VERIFY_URL).mock(
        return_value=httpx.Response(200, json={"verified": True, "payment_id": PAYMENT_ID})
    )
    async with _client() as ac:
        response = await ac.post(
            "/v1/verdict/So11111111111111111111111111111111111111112",
            headers={"X-Payment-ID": PAYMENT_ID},
        )
    assert response.status_code == 200
    body = response.json()
    # Honest abstention: no determination is sold on thin evidence.
    assert body["abstained"] is True
    assert body["determination"] == "unknown"
    assert body["abstention_reason"] == "insufficient data"
    assert body["safety_score"] is None
    assert body["known_factors"] < MIN_KNOWN_FACTORS
    # Evidence shape: every canonical named factor is present and typed.
    factor_names = [f["factor"] for f in body["factors"]]
    assert factor_names == list(VERDICT_FACTORS)
    for factor in body["factors"]:
        assert factor["status"] in {"known", "unknown"}
        if factor["status"] == "unknown":
            assert factor["value"] is None


@respx.mock
@pytest.mark.asyncio
async def test_verdict_issues_determination_when_enough_factors_known() -> None:
    """>= MIN_KNOWN_FACTORS known factors -> real determination + score."""
    _record_verified_payment()
    respx.post(VERIFY_URL).mock(
        return_value=httpx.Response(200, json={"verified": True, "payment_id": PAYMENT_ID})
    )
    # Feed the token-risk source enough named factors to clear the threshold.
    named = VERDICT_FACTORS[:8]
    risk_factors = [{"factor": n, "value": True, "evidence": f"detector:{n}"} for n in named]
    respx.post(f"{DEFAULT_FACILITATOR_URL}/v1/x402/verify").mock(
        return_value=httpx.Response(200, json={"verified": True})
    )
    # run_scan / token_forensics / honeypot_check are in-process tool calls
    # (no HTTP), so only coffer traffic is mocked; the risk source is patched
    # at the seam the offering actually consumes.
    from app.domain.x402 import offerings

    async def _rich_risk(token: str, chain: str = "ethereum") -> dict:
        return {"token_address": token, "risk_score": 35, "risk_factors": risk_factors}

    original = offerings.get_token_risk
    offerings.get_token_risk = _rich_risk
    try:
        async with _client() as ac:
            response = await ac.post(
                "/v1/verdict/So11111111111111111111111111111111111111112",
                headers={"X-Payment-ID": PAYMENT_ID},
            )
    finally:
        offerings.get_token_risk = original
    assert response.status_code == 200
    body = response.json()
    assert body["abstained"] is False
    assert body["determination"] in {"scam", "rug", "risky", "ok"}
    assert isinstance(body["safety_score"], int)
    known = [f for f in body["factors"] if f["status"] == "known"]
    assert len(known) >= MIN_KNOWN_FACTORS
    assert all(f["evidence"] for f in known)


@respx.mock
@pytest.mark.asyncio
async def test_dossier_evidence_shape() -> None:
    _record_verified_payment()
    respx.post(VERIFY_URL).mock(
        return_value=httpx.Response(200, json={"verified": True, "payment_id": PAYMENT_ID})
    )
    async with _client() as ac:
        response = await ac.get(
            "/v1/dossier/9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM",
            headers={"X-Payment-ID": PAYMENT_ID},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["offering"] == "deployer_dossier"
    for field in (
        "wallet_age_days",
        "total_deployments",
        "rug_rate",
        "funded_from",
        "sleep_pattern",
        "prior_rugged_tokens",
    ):
        assert field in body
    # Stubbed sources are DECLARED, never silently presented as real intel.
    assert "stubbed" in body["data_sources"]["deployer_track_record"]


@respx.mock
@pytest.mark.asyncio
async def test_cluster_evidence_shape_four_heuristics() -> None:
    _record_verified_payment()
    respx.post(VERIFY_URL).mock(
        return_value=httpx.Response(200, json={"verified": True, "payment_id": PAYMENT_ID})
    )
    async with _client() as ac:
        response = await ac.get(
            "/v1/cluster/9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM",
            headers={"X-Payment-ID": PAYMENT_ID},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["offering"] == "cluster_attribution"
    heuristic_types = [h["heuristic_type"] for h in body["heuristics"]]
    assert heuristic_types == list(CLUSTER_HEURISTICS)
    for h in body["heuristics"]:
        assert 0.0 <= h["confidence"] <= 1.0
    composite = body["composite_risk"]
    assert composite["score"] == (
        composite["label_risk"] + composite["cluster_risk"] + composite["deployer_risk"]
    )

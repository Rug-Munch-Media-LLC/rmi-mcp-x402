"""Spec-conformance tests for rmi-mcp-x402 against the official x402 402 flow.

Assertions follow ``docs/X402-SPEC-CONTRACT.md`` §2 and the authoritative
HTTP transport at ``docs/x402-official-spec/transports-v2__http.md``:

- unpaid GET → 402 with `PAYMENT-REQUIRED` (Base64 of the JSON body),
  `X-Payment-Required: true`, `X-Payment-Options: x402`, body == decoded
  header.
- every `accepts[]` entry carries scheme / network (CAIP-2) /
  maxAmountRequired (string) / payToAddress / asset.
- replay with a valid `X-Payment-ID` → 200; different caller → rejected;
  expired id → rejected.
- underpayment (atomic < required) → rejected; overpayment → accepted.
- nonce reuse (same tx_hash twice) → second rejected.
- scaffolded TRON payment never reports `verified: true`.
- `/v1/x402/health` exposes an honest VERIFICATION_STATUS.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import x402

SERVICE_PRICE_USD = 0.001  # mirrors conftest.StubFacilitator server price

SENDER_IP = "1.1.1.1"
OTHER_IP = "2.2.2.2"
CAIP2_BASE = "eip155:8453"

# USDC has 6 decimals; $0.001 == 1000 atomic units.
REQUIRED_ATOMIC = 1000


async def _issue(
    client: httpx.AsyncClient,
    tx_hash: str,
    *,
    network: str = CAIP2_BASE,
    asset: str = "USDC",
    amount_atomic: int = REQUIRED_ATOMIC,
) -> httpx.Response:
    """POST /v1/x402/pay claiming an on-chain payment of ``amount_atomic``."""
    return await client.post(
        "/v1/x402/pay",
        json={
            "payment_id": tx_hash,
            "tx_hash": tx_hash,
            "network": network,
            "asset": asset,
            "amount_atomic": str(amount_atomic),
        },
    )


def _decode_payment_required(raw: str) -> dict[str, Any]:
    return json.loads(base64.b64decode(raw).decode())


# ── §2.1 / §2.2 — the 402 response ──────────────────────────────────────────


async def test_unpaid_get_returns_spec_402(paid_client) -> None:
    async with paid_client(SENDER_IP) as client:
        r = await client.get("/v1/x402/paid")
    assert r.status_code == 402
    assert r.headers.get("X-Payment-Required") == "true"
    assert r.headers.get("X-Payment-Options") == "x402"

    raw = r.headers.get("PAYMENT-REQUIRED", "")
    assert raw, "PAYMENT-REQUIRED header must be present"
    decoded = _decode_payment_required(raw)
    assert decoded.get("x402Version") == 1
    assert isinstance(decoded.get("accepts"), list)
    # Body MUST equal the decoded header payload (same object both places).
    assert r.json() == decoded


async def test_402_accepts_entries_have_spec_fields(paid_client) -> None:
    async with paid_client(SENDER_IP) as client:
        r = await client.get("/v1/x402/paid")
    accepts = r.json()["accepts"]
    assert accepts, "accepts[] must be non-empty"
    for entry in accepts:
        assert "scheme" in entry and entry["scheme"] in ("exact", "upto")
        assert "network" in entry and ":" in entry["network"], entry  # CAIP-2 id
        assert isinstance(entry["maxAmountRequired"], str)
        assert entry["maxAmountRequired"].isdigit()
        assert entry["payToAddress"]
        assert entry["asset"]


async def test_402_content_type_json_and_accepts_network_is_caip2(paid_client) -> None:
    async with paid_client(SENDER_IP) as client:
        r = await client.get("/v1/x402/paid")
    assert r.headers.get("content-type", "").startswith("application/json")
    networks = {entry["network"] for entry in r.json()["accepts"]}
    assert networks, "accepts[] must carry CAIP-2 network ids"
    for network in networks:
        assert ":" in network, f"expected CAIP-2 network id, got {network!r}"


# ── §2.3 — replay / caller binding / expiry ─────────────────────────────────


async def test_replay_valid_payment_id_returns_200(x402) -> None:
    async with x402.caller(SENDER_IP) as client:
        r = await _issue(client, "0xabc123")
        assert r.status_code == 200, r.json()
        r = await client.get("/v1/x402/paid", headers={"X-Payment-ID": "0xabc123"})
    assert r.status_code == 200
    assert r.json() == {"data": "ok"}


async def test_replay_from_different_caller_rejected(x402) -> None:
    async with x402.caller(SENDER_IP) as alice:
        await _issue(alice, "0xabc456")
    async with x402.caller(OTHER_IP) as bob:
        r = await bob.get("/v1/x402/paid", headers={"X-Payment-ID": "0xabc456"})
    assert r.status_code == 402
    assert r.json().get("x402Version") == 1


async def test_expired_payment_id_rejected(x402) -> None:
    x402.inject_expired_payment("0xexpired1", caller_key=SENDER_IP)
    async with x402.caller(SENDER_IP) as client:
        r = await client.get("/v1/x402/paid", headers={"X-Payment-ID": "0xexpired1"})
    assert r.status_code == 402  # expired → must pay again


# ── §4 — server-authoritative pricing: under/over payment ───────────────────


async def test_underpayment_rejected(x402) -> None:
    async with x402.caller(SENDER_IP) as client:
        r = await _issue(client, "0xunder", amount_atomic=REQUIRED_ATOMIC - 1)
        assert r.status_code == 402
        assert r.json().get("verified") is False
        assert "insufficient_funds" in r.json().get("reason", "")
    # And no payment_id was recorded as paid → replay still 402.
    async with x402.caller(SENDER_IP) as client:
        r = await client.get("/v1/x402/paid", headers={"X-Payment-ID": "0xunder"})
    assert r.status_code == 402


async def test_overpayment_accepted(x402) -> None:
    async with x402.caller(SENDER_IP) as client:
        r = await _issue(client, "0xover", amount_atomic=REQUIRED_ATOMIC * 5)
        assert r.status_code == 200
        assert r.json().get("verified") is True
        r = await client.get("/v1/x402/paid", headers={"X-Payment-ID": "0xover"})
    assert r.status_code == 200


# ── §5 — nonce reuse (fresh ks nonce store) ─────────────────────────────────


async def test_nonce_reuse_rejected_second_tx(x402) -> None:
    async with x402.caller(SENDER_IP) as client:
        first = await _issue(client, "0xnonce1")
        assert first.status_code == 200
        second = await _issue(client, "0xnonce1")  # same tx_hash again
    assert second.status_code == 402
    assert second.json().get("verified") is False
    assert "nonce_already_used" in second.json().get("reason", "")


# ── §5.3 / §6 — scaffolded TRON never verified:true ─────────────────────────


async def test_tron_scaffolded_never_verified(x402) -> None:
    async with x402.caller(SENDER_IP) as client:
        r = await _issue(
            client,
            "0x_tron_1",
            network="tron:mainnet",
            asset="TRX",
            amount_atomic=REQUIRED_ATOMIC * 10,
        )
    assert r.status_code == 402
    assert r.json().get("verified") is False
    assert "needs_facilitator" in r.json().get("reason", "")


# ── §6 — honest verification status ─────────────────────────────────────────


async def test_health_reports_honest_verification_status(x402) -> None:
    async with x402.caller(SENDER_IP) as client:
        r = await client.get("/v1/x402/health")
    assert r.status_code == 200
    body = r.json()
    by_network = body["verification_status_by_network"]
    # A scaffolded chain is NEVER reported as live.
    assert by_network.get("tron") == "scaffolded:needs_facilitator"
    assert (
        body["verification_status"].get("tron", {}).get("status") == "scaffolded:needs_facilitator"
    )
    assert body["verification_status"].get("tron", {}).get("real") is False


# ── §5.3 — service asserts the raw contract samples too ─────────────────────


def test_server_price_is_quotable_to_atomic() -> None:
    # Sanity anchor: $0.001 USDC on base == 1000 atomic (6 decimals). The
    # harness and under/over-payment tests depend on this invariant.
    assert int(x402.asset_to_atomic(SERVICE_PRICE_USD, "USDC", CAIP2_BASE)) == REQUIRED_ATOMIC

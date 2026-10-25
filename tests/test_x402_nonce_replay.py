"""Replay protection: EIP-3009 nonce is primary, tx_hash is second guard."""
import base64
import json
import time

import pytest

from app.domain.x402.facilitator_core import FacilitatorCoreMixin
from app.domain.x402.nonce_guard import nonce_replay_key, validity_error
from app.domain.x402.payment_headers import resolve_payment_proof
from app.domain.x402.pricing import normalize_network_id
from app.domain.x402.replay import ReplayStore

NONCE_A = "0x" + "11" * 32
NONCE_B = "0x" + "22" * 32
PAYER = "0xAbCdEf0000000000000000000000000000000001"


def _guard(auths=None):
    guard = object.__new__(FacilitatorCoreMixin)
    guard.replay = ReplayStore(ttl=60)
    guard._pending_auths = auths or {}
    return guard


def _auth(nonce, before=None, after=0):
    return {
        "from": PAYER,
        "nonce": nonce,
        "validAfter": str(after),
        "validBefore": str(before if before is not None else int(time.time()) + 600),
    }


@pytest.mark.asyncio
async def test_replayed_nonce_rejected_before_settle():
    guard = _guard({"p1": _auth(NONCE_A)})
    ok, _ = await guard._enforce_replay("0xtx1", "base", "p1")
    assert ok is True
    ok, reason = await guard._enforce_replay("0xtx1", "base", "p1")
    assert ok is False
    assert "nonce" in reason


@pytest.mark.asyncio
async def test_different_nonce_same_tx_hash_rejected():
    guard = _guard({"p1": _auth(NONCE_A), "p2": _auth(NONCE_B)})
    assert (await guard._enforce_replay("0xtx9", "base", "p1"))[0] is True
    ok, reason = await guard._enforce_replay("0xtx9", "base", "p2")
    assert ok is False
    assert "transaction already used" in reason


@pytest.mark.asyncio
async def test_expired_authorization_rejected():
    guard = _guard({"p1": _auth(NONCE_B, before=int(time.time()) - 1)})
    ok, reason = await guard._enforce_replay("0xtx2", "base", "p1")
    assert ok is False
    assert "expired" in reason


@pytest.mark.asyncio
async def test_not_yet_valid_authorization_rejected():
    guard = _guard({"p1": _auth(NONCE_B, after=int(time.time()) + 600)})
    ok, reason = await guard._enforce_replay("0xtx3", "base", "p1")
    assert ok is False
    assert "not yet valid" in reason


def test_validity_window_malformed():
    assert validity_error({"validBefore": "not-a-number"}) == (
        "authorization validity window malformed"
    )


def test_base_network_maps_to_caip2():
    assert normalize_network_id("base") == "eip155:8453"
    assert normalize_network_id("BASE") == "eip155:8453"
    assert nonce_replay_key("base", "USDC", PAYER, NONCE_A).startswith(
        "x402:nonce:eip155:8453:usdc:"
    )


def _v2_header(nonce):
    payload = {
        "x402Version": 2,
        "network": "eip155:8453",
        "payload": {"authorization": {"from": PAYER, "nonce": nonce}},
    }
    return base64.b64encode(json.dumps(payload).encode()).decode()


def test_verify_accepts_x_payment_id_header():
    proof = resolve_payment_proof({"X-Payment-ID": "pay_123"})
    assert proof["payment_id"] == "pay_123"
    assert proof["version"] == 0


def test_verify_accepts_v2_payment_signature_header():
    proof = resolve_payment_proof({"PAYMENT-SIGNATURE": _v2_header(NONCE_A)})
    assert proof["version"] == 2
    assert proof["header"] == "PAYMENT-SIGNATURE"
    assert proof["payment_id"].startswith("auth:eip155:8453:")
    assert NONCE_A in proof["payment_id"]


def test_verify_accepts_v1_x_payment_header():
    proof = resolve_payment_proof({"X-PAYMENT": _v2_header(NONCE_B)})
    assert proof["version"] == 1
    assert NONCE_B in proof["payment_id"]


def test_payment_id_wins_when_both_headers_present():
    proof = resolve_payment_proof(
        {"X-Payment-ID": "pay_456", "X-PAYMENT": _v2_header(NONCE_A)}
    )
    assert proof["payment_id"] == "pay_456"

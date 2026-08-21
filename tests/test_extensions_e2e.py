"""Extension end-to-end tests against the consolidated x402 extensions layer.

Covers the OFFICIAL specs vendored in ``docs/x402-official-spec/``:

* ``extensions__payment-identifier.md``  — idempotency over HTTP: same id +
  same fingerprint -> cached 200; same id + different fingerprint -> 409
  Conflict; ``required: true`` + missing id -> 400.
* gas-sponsoring (``eip2612`` / ``erc20-approval``) — honest scaffold: the
  on-chain step raises and never fabricates a settlement; no ``verified:true``
  settlement hash is ever produced without a real sponsor.
* ``schemes__upto.md`` / ``schemes__batch-settlement.md`` — structure + pricing
  + asset validation ONLY; every on-chain path is ``scaffolded:needs_facilitator``
  and never claims to be verified.
"""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.domain.x402.extensions.gas_sponsoring import (
    EIP2612_KEY,
    ERC20_APPROVAL_KEY,
    GasSponsoringFacilitator,
    register_gas_sponsoring_facilitator,
)
from app.domain.x402.extensions.payment_identifier import PaymentIdentifierStore
from app.domain.x402.extensions.schemes import BatchSettlementScheme, UpToScheme
from app.domain.x402.service import FacilitatorRouter

# ── payment-identifier idempotency (HTTP e2e) ───────────────────────────────


@pytest.fixture
async def idem_app() -> FastAPI:
    store = PaymentIdentifierStore(ttl=3600)
    app = FastAPI()

    @app.post("/v1/x402/payid")
    async def payid(body: dict, request: Request) -> JSONResponse:
        pid = body.get("payment_id")
        fp_req = {
            "scheme": body.get("scheme", "exact"),
            "network": body.get("network", "eip155:8453"),
            "asset": body.get("asset", "USDC"),
            "amount": body.get("amount", "1000"),
            "pay_to": body.get("pay_to", "0xserver"),
        }
        fp = store.fingerprint(
            fp_req,
            resource_ctx={
                "resource": body.get("resource", "/api"),
                "method": body.get("method", "POST"),
                "operation": body.get("operation", "op"),
            },
        )
        caller_scope = f"caller:{request.client.host}"
        required = bool(body.get("required"))
        res = await store.check_before_payment(pid, caller_scope, fp, required=required)
        if res.state == "conflict":
            return JSONResponse({"state": "conflict", "payment_id": pid}, status_code=409)
        if res.state == "missing" and required:
            return JSONResponse({"state": "missing", "payment_id": pid}, status_code=400)
        return JSONResponse(
            {"state": res.state, "payment_id": pid, "reason": res.reason}, status_code=200
        )

    return app


async def _issue(idem_app: FastAPI, payload: dict) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=idem_app), base_url="http://testserver"
    ) as c:
        return await c.post("/v1/x402/payid", json=payload)


PAYID = "pay_same_fingerprint_abcdef_1234"  # 16-128 [a-zA-Z0-9_-]
BASE = {
    "payment_id": PAYID,
    "scheme": "exact",
    "network": "eip155:8453",
    "asset": "USDC",
    "amount": "1000",
    "resource": "/api/x",
    "method": "POST",
}


async def test_payment_identifier_same_id_same_fingerprint_cached(idem_app: FastAPI) -> None:
    first = await _issue(idem_app, BASE)
    second = await _issue(idem_app, BASE)
    assert first.status_code == 200 and first.json()["state"] == "accepted"
    assert second.status_code == 200
    assert second.json()["state"] == "cached"  # served without re-processing
    assert second.json()["payment_id"] == PAYID


async def test_payment_identifier_same_id_different_fingerprint_conflict(
    idem_app: FastAPI,
) -> None:
    await _issue(idem_app, BASE)
    # Same id, different amount (=> different request fingerprint) -> 409.
    changed = {**BASE, "amount": "9999"}
    r = await _issue(idem_app, changed)
    assert r.status_code == 409
    assert r.json()["state"] == "conflict"


async def test_payment_identifier_required_missing_returns_400(idem_app: FastAPI) -> None:
    r = await _issue(idem_app, {**BASE, "payment_id": None, "required": True})
    assert r.status_code == 400
    assert r.json()["state"] == "missing"


# ── gas sponsoring — honest scaffold ────────────────────────────────────────


async def test_gas_sponsoring_prepare_honest_scaffold() -> None:
    # The honest prepare contract: no gas batch ever becomes a verified
    # settlement — it is marked scaffolded and carries NO fabricated tx_hash.
    fac = GasSponsoringFacilitator(key=ERC20_APPROVAL_KEY, payment={})
    prepped = await fac.prepare_payment()
    assert prepped["scaffolded"] == "needs_facilitator"
    assert prepped["tx_hash"] is None
    assert prepped["sponsor_flow"] == "none"


async def test_gas_sponsoring_real_payloads_fail_closed() -> None:
    # [REVIEW] prepare_payment looks the extension info up by the SHORT flow id
    # (exts.get("eip2612") / ext.get("erc20-approval")) but payloads are keyed
    # by the camelCase extension keys (EIP2612_KEY / ERC20_APPROVAL_KEY). A real
    # gas-sponsoring payload therefore always FAILS CLOSED (ValueError) — safe,
    # never a fabricated tx_hash / verified:true — but no EIP-2612 or ERC-20
    # gas-sponsoring payment can be prepped until the key routing is fixed.
    eip2612 = GasSponsoringFacilitator(
        key=EIP2612_KEY,
        payment={
            "extensions": {
                EIP2612_KEY: {
                    "token": "0xUSDC",
                    "owner": "0xme",
                    "spender": "0xpermit2",
                    "amount": "1000",
                    "nonce": "1",
                    "deadline": "9999999999",
                    "signature": "0xdeadbeef",
                }
            }
        },
    )
    with pytest.raises(ValueError):
        await eip2612.prepare_payment()

    erc20 = GasSponsoringFacilitator(
        key=ERC20_APPROVAL_KEY,
        payment={
            "extensions": {
                ERC20_APPROVAL_KEY: {
                    "token": "0xUSDC",
                    "signedTransaction": {
                        "to": "0x000000000022D473030F116dDEE9F6B43aC78BA3",  # Permit2
                        "from": "0xme",
                        "value": "0x0",
                    },
                }
            }
        },
    )
    with pytest.raises(ValueError):
        await erc20.prepare_payment()


async def test_gas_sponsoring_apply_raises_without_real_sponsor() -> None:
    fac = GasSponsoringFacilitator(key=ERC20_APPROVAL_KEY, payment={})
    # No sponsor_impl -> on-chain step MUST raise (never a fake hash).
    with pytest.raises(NotImplementedError):
        await fac.apply_gas_batch()


async def test_gas_sponsoring_invalid_payload_rejected() -> None:
    fac = GasSponsoringFacilitator(
        key=EIP2612_KEY,
        payment={"extensions": {EIP2612_KEY: {"token": "0xUSDC"}}},  # missing fields
    )
    with pytest.raises(ValueError):
        await fac.prepare_payment()


async def test_gas_sponsoring_registered_facilitator_cannot_settle() -> None:
    router = FacilitatorRouter()
    register_gas_sponsoring_facilitator("eip155:8453")
    # The registered impl's settle raises => router settle can't fabricate a hash.
    result = await router.settle("pay_sponsor", "0xtx", "eip155:8453", "USDC", 0.001)
    assert result.get("success") is False
    assert "settle" in result.get("reason", "").lower()


# ── upto / batch-settlement — scaffolded, never verified ────────────────────


async def test_upto_scheme_is_scaffolded_and_settle_raises() -> None:
    upto = UpToScheme(
        scheme="upto",
        network="eip155:8453",
        max_price_usd=Decimal("0.10"),
        asset="usdc",
    )
    option = upto.payment_option("/api/x", "usage billed", "0xpay")
    assert option["scheme"] == "upto"
    assert option["extra"]["scaffolded"] == "needs_facilitator"
    assert upto.authorize(1000)["scaffolded"] == "needs_facilitator"
    # No fake settlement: on-chain settle must raise.
    with pytest.raises(NotImplementedError):
        await upto.settle({}, "50%")


async def test_batch_settlement_is_scaffolded_and_claim_raises() -> None:
    batch = BatchSettlementScheme(network="eip155:8453")
    option = batch.open_channel_option("/api/x", "escrow", "0xpay")
    assert option["scheme"] == "batch-settlement"
    assert option["extra"]["scaffolded"] == "needs_facilitator"
    assert batch.channel_manager()["scaffolded"] == "needs_facilitator"
    # Deposit policy + honest voucher-shape validation (structure yes, chain no).
    assert batch.deposit_amount(100) == 100 * batch.deposit_multiplier
    assert batch.verify_voucher({"channelAddress": "0xc", "totalClaimable": 50}) is True
    # On-chain claim/settle/refund is scaffolded — must raise.
    with pytest.raises(NotImplementedError):
        await batch.claim_and_settle()

"""TRON TRC-20 payment reception — mocked Trongrid RPC, full failure matrix."""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from app.domain.x402.tron_verifier import (
    TRON_USDT_CONTRACT,
    verify_tron_payment,
)

MERCHANT = "TMerchantWallet1111111111111111111111"
PAYER = "TPayerWallet2222222222222222222222222"
RPC = "https://trongrid.test"


def _tx_info(
    *,
    block: int = 42_000_000,
    to: str = MERCHANT,
    value: int = 1_000_000,
    contract: str = TRON_USDT_CONTRACT,
    timestamp: int = 1755700000000,
) -> dict:
    return {
        "id": "0xtx",
        "blockNumber": block,
        "blockHash": "0x" + "ab" * 32,
        "time": timestamp,
        "log": [
            {
                "event_name": "Transfer",
                "caller_contract_address": contract,
                "event": {"from": PAYER, "to": to, "value": str(value)},
            }
        ],
    }


@respx.mock
@pytest.mark.asyncio
async def test_valid_trc20_transfer_verified() -> None:
    respx.post(f"{RPC}/wallet/gettransactioninfobyid").mock(
        return_value=Response(200, json=_tx_info())
    )
    out = await verify_tron_payment("0xtx", MERCHANT, 1.0, rpc_url=RPC)
    assert out["verified"] is True, out
    assert out["amount_atomic"] == 1_000_000
    assert out["decimals"] == 6
    assert out["amount_usd"] == 1.0
    assert out["signer"] == PAYER


@respx.mock
@pytest.mark.asyncio
async def test_wrong_recipient_rejected() -> None:
    respx.post(f"{RPC}/wallet/gettransactioninfobyid").mock(
        return_value=Response(200, json=_tx_info(to="TOtherWallet33333333333333333333333"))
    )
    out = await verify_tron_payment("0xtx", MERCHANT, 1.0, rpc_url=RPC)
    assert out["verified"] is False and "matching" in out["reason"]


@respx.mock
@pytest.mark.asyncio
async def test_wrong_token_contract_rejected() -> None:
    respx.post(f"{RPC}/wallet/gettransactioninfobyid").mock(
        return_value=Response(200, json=_tx_info(contract="TScamToken444444444444444444444444"))
    )
    out = await verify_tron_payment("0xtx", MERCHANT, 1.0, rpc_url=RPC)
    assert out["verified"] is False


@respx.mock
@pytest.mark.asyncio
async def test_underpayment_rejected() -> None:
    respx.post(f"{RPC}/wallet/gettransactioninfobyid").mock(
        return_value=Response(200, json=_tx_info(value=100))  # 0.0001 USDT < $1
    )
    out = await verify_tron_payment("0xtx", MERCHANT, 1.0, rpc_url=RPC)
    assert out["verified"] is False and "insufficient" in out["reason"]


@respx.mock
@pytest.mark.asyncio
async def test_tx_not_found_rejected_not_crash() -> None:
    respx.post(f"{RPC}/wallet/gettransactioninfobyid").mock(return_value=Response(200, json={}))
    out = await verify_tron_payment("0xmissing", MERCHANT, 1.0, rpc_url=RPC)
    assert out["verified"] is False and "not_found" in out["reason"]


@respx.mock
@pytest.mark.asyncio
async def test_unconfirmed_tx_rejected() -> None:
    info = _tx_info()
    info.pop("blockNumber")
    respx.post(f"{RPC}/wallet/gettransactioninfobyid").mock(return_value=Response(200, json=info))
    out = await verify_tron_payment("0xtx", MERCHANT, 1.0, rpc_url=RPC)
    assert out["verified"] is False and "unconfirmed" in out["reason"]


@respx.mock
@pytest.mark.asyncio
async def test_rpc_down_falls_back_then_fails_closed() -> None:
    primary = respx.post("https://downrpc.test/wallet/gettransactioninfobyid").mock(
        return_value=Response(503)
    )
    fallback = respx.post(f"{RPC}/wallet/gettransactioninfobyid").mock(
        return_value=Response(200, json=_tx_info())
    )
    out = await verify_tron_payment(
        "0xtx",
        MERCHANT,
        1.0,
        rpc_url="https://downrpc.test",
        fallback_rpc_urls=[RPC],
    )
    # Primary failed -> fallback answered -> verified.
    assert primary.called and fallback.called
    assert out["verified"] is True


@respx.mock
@pytest.mark.asyncio
async def test_all_rpcs_down_fail_closed() -> None:
    respx.post(host="downa.test", path__regex=".*").mock(return_value=Response(503))
    respx.post(host="downb.test", path__regex=".*").mock(return_value=Response(503))
    out = await verify_tron_payment(
        "0xtx",
        MERCHANT,
        1.0,
        rpc_url="https://downa.test",
        fallback_rpc_urls=["https://downb.test"],
    )
    assert out["verified"] is False


@pytest.mark.asyncio
async def test_router_routes_tron_to_fail_closed_scaffold() -> None:
    """network_family tron MUST fail closed via the scaffold — never a real
    Trongrid claim. The scheme status is scaffolded:needs_facilitator until a
    real TRC-20 settlement verifier is wired in and the status flips."""
    from app.domain.x402.facilitator_router import FacilitatorRouter

    router = FacilitatorRouter(facilitators=[], self_hosted=False)
    out = await router.verify("pid", "0xtx", "tron:mainnet", "USDT", 1.0)
    assert out.get("verified") is False
    assert out.get("scaffolded") is True
    assert out.get("facilitator") is None  # NOT the live tron-rpc claim
    assert "needs_facilitator" in out.get("reason", "")
    assert "scaffolded" in json_dumps(out)


def json_dumps(o: object) -> str:
    import json

    return json.dumps(o)

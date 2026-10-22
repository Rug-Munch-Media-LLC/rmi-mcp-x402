"""Payment-path tests: 402 challenge, free tier, replay, prepaid, no-settle-no-data."""

from __future__ import annotations

import base64
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from store_harness import (  # noqa: E402  # pyright: ignore[reportImplicitRelativeImport]
    ARGS,
    TOOL,
    build,
    call,
)

from store.catalog import get_tool  # noqa: E402


def _proof(payment_id: str, tx_hash: str, network: str, amount: float) -> dict[str, str]:
    payload = {
        "paymentId": payment_id,
        "txHash": tx_hash,
        "network": network,
        "asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "amountUsd": amount,
    }
    return {"X-PAYMENT": base64.b64encode(json.dumps(payload).encode()).decode()}


def _price() -> float:
    spec = get_tool(TOOL)
    assert spec is not None
    return spec.price_usd


def test_free_tier_call_succeeds_then_402(tmp_path, monkeypatch):
    client = build(tmp_path, monkeypatch).client
    first = call(client, TOOL, ARGS)
    assert first.status_code == 200
    result = first.json()["result"]
    assert result["receipt"]["mode"] == "free_tier"
    assert result["structuredContent"]["ok"] is True

    spec = get_tool(TOOL)
    assert spec is not None
    for _ in range(spec.free_daily - 1):
        assert call(client, TOOL, ARGS).status_code == 200

    exhausted = call(client, TOOL, ARGS)
    assert exhausted.status_code == 402
    challenge = exhausted.json()["error"]["data"]["challenge"]
    assert challenge["x402Version"] == 1
    entry = challenge["accepts"][0]
    assert entry["network"] == "eip155:8453"
    assert entry["asset"] == "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    assert entry["payTo"].startswith("0x")
    assert entry["maxAmountRequired"] == str(spec.price_atoms)
    assert challenge["paymentId"]
    header = exhausted.headers["PAYMENT-REQUIRED"]
    assert json.loads(base64.b64decode(header))["paymentId"] == challenge["paymentId"]
    assert challenge["accepts"][1]["network"] == "eip155:84532"


def test_underpayment_rejected_without_serving(tmp_path, monkeypatch):
    h = build(tmp_path, monkeypatch)
    response = call(h.client, TOOL, ARGS, _proof("deadbeef", "0xabc", h.settings.network, 0.0001))
    assert response.status_code == 402
    assert response.json()["error"]["data"]["reason"] == "invalid_payment_proof"
    assert "result" not in response.json()


def test_settlement_failure_serves_no_data(tmp_path, monkeypatch):
    h = build(tmp_path, monkeypatch)

    async def failing_verify(base_url, body):
        return {"ok": False, "reason": "tx_not_found", "status": 200}

    monkeypatch.setattr("store.coffer.verify", failing_verify)
    response = call(h.client, TOOL, ARGS, _proof("pay-1", "0xfeed", h.settings.network, _price()))
    assert response.status_code == 402
    assert response.json()["error"]["data"]["reason"] == "payment_verification_failed"
    assert "result" not in response.json(), "an unsettled payment must never return data"


def test_coffer_payload_uses_asset_symbol(tmp_path, monkeypatch):
    """Coffer invoices by symbol; a contract address is rejected as unsupported."""
    h = build(tmp_path, monkeypatch)
    seen: dict[str, object] = {}

    async def spy(base_url, body):
        seen.update(body)
        return {"ok": False, "reason": "stop", "status": 200}

    monkeypatch.setattr("store.coffer.verify", spy)
    call(h.client, TOOL, ARGS, _proof("sym-1", "0xaa", h.settings.network, _price()))
    assert seen["asset"] == "USDC"
    assert seen["network"] == h.settings.network
    amount = seen["amount_usd"]
    assert isinstance(amount, (int, float))
    assert float(amount) >= _price()


def test_replay_of_consumed_payment_id_is_rejected(tmp_path, monkeypatch):
    h = build(tmp_path, monkeypatch)
    assert h.db.claim("payment:pay-replay") is True

    async def ok_verify(base_url, body):
        return {"ok": True, "reason": "", "status": 200, "raw": {}}

    monkeypatch.setattr("store.coffer.verify", ok_verify)
    response = call(
        h.client, TOOL, ARGS, _proof("pay-replay", "0xdead", h.settings.network, _price())
    )
    assert response.status_code == 409
    assert response.json()["error"]["data"]["reason"] == "payment_already_used"
    assert "result" not in response.json()


def test_settled_payment_serves_data_and_blocks_replay(tmp_path, monkeypatch):
    h = build(tmp_path, monkeypatch)
    calls = {"settle": 0}

    async def ok_verify(base_url, body):
        return {"ok": True, "reason": "", "status": 200, "raw": {}}

    async def ok_settle(base_url, body):
        calls["settle"] += 1
        return {"ok": True, "reason": "", "tx_hash": "0xsettled", "status": 200, "raw": {}}

    monkeypatch.setattr("store.coffer.verify", ok_verify)
    monkeypatch.setattr("store.coffer.settle", ok_settle)
    headers = _proof("pay-ok", "0xsettle", h.settings.network, _price())

    first = call(h.client, TOOL, ARGS, headers)
    assert first.status_code == 200
    receipt = first.json()["result"]["receipt"]
    assert receipt["mode"] == "x402"
    assert receipt["tx_hash"] == "0xsettled"
    assert calls["settle"] == 1

    reap = call(h.client, TOOL, ARGS, headers)
    assert reap.status_code == 409
    assert calls["settle"] == 1, "a replayed payment must never settle twice"


def test_daily_budget_caps_spend(tmp_path, monkeypatch):
    h = build(tmp_path, monkeypatch)

    async def ok_verify(base_url, body):
        return {"ok": True, "reason": "", "status": 200, "raw": {}}

    async def ok_settle(base_url, body):
        return {"ok": True, "reason": "", "tx_hash": "0xb", "status": 200, "raw": {}}

    monkeypatch.setattr("store.coffer.verify", ok_verify)
    monkeypatch.setattr("store.coffer.settle", ok_settle)
    h.db.add_spend("ip:testclient", dt.datetime.now(dt.UTC).strftime("%Y-%m-%d"), 1_000_000)
    response = call(
        h.client, TOOL, ARGS, _proof("pay-budget", "0xbb", h.settings.network, _price())
    )
    assert response.status_code == 429
    assert response.json()["error"]["data"]["reason"] == "daily_budget_exceeded"


def test_prepaid_api_key_debits_balance(tmp_path, monkeypatch):
    from store import apikeys

    h = build(tmp_path, monkeypatch)
    raw = apikeys.issue_key()
    h.db.create_key(apikeys.hash_key(raw), "test", 50_000)
    response = call(h.client, TOOL, ARGS, {"X-API-Key": raw})
    assert response.status_code == 200
    receipt = response.json()["result"]["receipt"]
    assert receipt["mode"] == "prepaid"
    assert receipt["balanceUsd"] < 0.05


def test_invalid_api_key_rejected(tmp_path, monkeypatch):
    h = build(tmp_path, monkeypatch)
    response = call(h.client, TOOL, ARGS, {"X-API-Key": "rmi_store_" + "z" * 40})
    assert response.status_code == 401
    assert response.json()["error"]["data"]["reason"] == "invalid_api_key"


def test_unknown_tool_is_404(tmp_path, monkeypatch):
    h = build(tmp_path, monkeypatch)
    assert call(h.client, "does.not.exist", {}).status_code == 404

"""intel_wallet_profile and the two sources it summarises directly.

The profile composes live RPC balances, Postgres labels, archive history and
risk flags. Every collaborator is injected through `IntelDeps`, so these tests
run offline with no patching and no live database.

Contract points: the profile always has the same six keys in contract order;
one failing source degrades that section only; a Postgres error tag no longer
crashes the risk step (regression).
"""

from __future__ import annotations

import pytest
from intel_harness import (
    EVM_ADDR,
    PROFILE_KEYS,
    SOL_ADDR,
    TAG,
    TX,
    FakeDuck,
    FakePG,
    FakeRPC,
    deps,
)

import data_mcp_intel_wallet as wallet
from data_mcp_intel_balances import live_balances
from data_mcp_intel_risk import risk_signals


# ── live balances ─────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_balances_read_solana_for_a_solana_address() -> None:
    rpc = FakeRPC({"getBalance": {"ok": True, "result": 2_000_000_000, "endpoint": "helius"}})
    out = await live_balances(SOL_ADDR, deps(rpc=rpc))
    assert out == {"solana": {"sol": 2.0, "lamports": 2_000_000_000, "endpoint": "helius"}}


@pytest.mark.asyncio
async def test_balances_read_every_evm_chain_for_an_evm_address() -> None:
    rpc = FakeRPC(
        {"eth_getBalance": {"ok": True, "result": "0xde0b6b3a7640000", "endpoint": "drpc"}}
    )
    out = await live_balances(EVM_ADDR, deps(rpc=rpc))
    assert set(out) == {"ethereum", "base", "arbitrum"}
    assert out["ethereum"]["eth"] == 1.0
    assert out["ethereum"]["wei"] == 10**18


@pytest.mark.asyncio
async def test_balances_omit_zero_balance_chains() -> None:
    rpc = FakeRPC({"eth_getBalance": {"ok": True, "result": "0x0", "endpoint": "drpc"}})
    assert await live_balances(EVM_ADDR, deps(rpc=rpc)) == {}


@pytest.mark.asyncio
async def test_balances_are_empty_when_every_endpoint_fails() -> None:
    rpc = FakeRPC({"getBalance": {"ok": False, "error": "down", "endpoint": None}})
    assert await live_balances(SOL_ADDR, deps(rpc=rpc)) == {}


# ── profile ───────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_profile_has_the_contract_shape_in_order() -> None:
    result = await wallet.profile(SOL_ADDR, deps())
    assert list(result) == PROFILE_KEYS
    assert result["address"] == SOL_ADDR
    assert result["live_balances"] == {}
    assert result["entity_tags"] == []
    assert result["historical_patterns"] == {}
    assert result["risk_signals"] == []


@pytest.mark.asyncio
async def test_profile_combines_all_four_sources() -> None:
    rpc = FakeRPC(
        {
            "getBalance": {"ok": True, "result": 1_000_000_000, "endpoint": "helius"},
            "getTokenAccountsByOwner": {
                "ok": True,
                "result": {"value": [{"id": i} for i in range(600)]},
                "endpoint": "helius",
            },
        }
    )
    out = await wallet.profile(
        SOL_ADDR, deps(rpc=rpc, pg=FakePG(rows=[TAG]), duck=FakeDuck(records=[TX, TX]), token="t")
    )
    assert out["live_balances"]["solana"]["sol"] == 1.0
    assert out["entity_tags"] == [
        {
            "label": "scammer",
            "confidence": 95.0,
            "chain": "ethereum",
            "source": "manual",
            "entity": "Bad Corp",
            "entity_type": "exchange",
        }
    ]
    assert out["historical_patterns"]["total_matches"] == 2
    assert out["historical_patterns"]["unique_counterparties"] == 1
    assert out["risk_signals"] == ["high_token_count", "known_malicious"]


@pytest.mark.asyncio
async def test_profile_degrades_one_source_at_a_time() -> None:
    """A dead Postgres, dead archives and dead RPC still produce a profile."""
    out = await wallet.profile(SOL_ADDR, deps(pg=None, token=""))
    assert list(out) == PROFILE_KEYS
    assert out["entity_tags"] == []
    assert out["historical_patterns"] == {}


@pytest.mark.asyncio
async def test_profile_survives_a_label_error_without_crashing_risk() -> None:
    """Regression: an error tag used to raise KeyError in the risk step."""
    out = await wallet.profile(SOL_ADDR, deps(pg=FakePG(error=RuntimeError("pg gone"))))
    assert out["entity_tags"] == [{"error": "pg gone"}]
    assert out["risk_signals"] == []


# ── risk signals ──────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_risk_flags_a_zero_token_wallet() -> None:
    rpc = FakeRPC({"getTokenAccountsByOwner": {"ok": True, "result": {"value": []}}})
    flags = await risk_signals(SOL_ADDR, {"solana": {"sol": 1.0}}, [], deps(rpc=rpc))
    assert flags == ["no_tokens"]


@pytest.mark.asyncio
async def test_risk_ignores_non_malicious_labels() -> None:
    assert await risk_signals(SOL_ADDR, {}, [{"label": "exchange"}], deps()) == []

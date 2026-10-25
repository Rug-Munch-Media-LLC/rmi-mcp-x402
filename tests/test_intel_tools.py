"""Intelligence tools: the wallet profile and its four sources.

The profile composes live RPC balances, Postgres labels, DuckDB archive
history and risk flags. Every collaborator is injected through `IntelDeps`,
so these tests run offline with no patching and no live database.

Contract points proven here:
  * the profile always has the same six keys, in contract order;
  * one failing source degrades that section only — it never raises and never
    invents data;
  * a Postgres error tag no longer crashes the risk step (regression);
  * every typed `ok:false` answer is returned, never swallowed.
"""

from __future__ import annotations

import pytest
from intel_harness import FakeDuck, FakePG, FakeRPC, deps

import data_mcp_intel_duck as duck_tool
import data_mcp_intel_entity as entity_tool
import data_mcp_intel_history as history
import data_mcp_intel_schema as schema_tool
import data_mcp_intel_wallet as wallet
from data_mcp_intel_balances import live_balances
from data_mcp_intel_risk import risk_signals

SOL_ADDR = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
EVM_ADDR = "0xAbC0000000000000000000000000000000000001"
PROFILE_KEYS = [
    "address",
    "queried_at",
    "live_balances",
    "entity_tags",
    "historical_patterns",
    "risk_signals",
]
TAG = {
    "wallet_address": "0xAbC0000000000000000000000000000000000001",
    "label_tag": "scammer",
    "confidence_score": 95,
    "chain": "ethereum",
    "source": "manual",
    "entity_name": "Bad Corp",
    "entity_type": "exchange",
    "description": "labelled by hand",
}
TX = {
    "block_time": "2026-01-02T00:00:00Z",
    "from_address": EVM_ADDR,
    "to_address": "0x0000000000000000000000000000000000000002",
}


# ── live balances ─────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_live_balances_reads_solana_for_a_solana_address() -> None:
    rpc = FakeRPC({"getBalance": {"ok": True, "result": 2_000_000_000, "endpoint": "helius"}})
    out = await live_balances(SOL_ADDR, deps(rpc=rpc))
    assert out == {"solana": {"sol": 2.0, "lamports": 2_000_000_000, "endpoint": "helius"}}


@pytest.mark.asyncio
async def test_live_balances_reads_every_evm_chain_for_an_evm_address() -> None:
    rpc = FakeRPC(
        {"eth_getBalance": {"ok": True, "result": "0xde0b6b3a7640000", "endpoint": "drpc"}}
    )
    out = await live_balances(EVM_ADDR, deps(rpc=rpc))
    assert set(out) == {"ethereum", "base", "arbitrum"}
    assert out["ethereum"]["eth"] == 1.0
    assert out["ethereum"]["wei"] == 10**18


@pytest.mark.asyncio
async def test_live_balances_omits_zero_balance_chains() -> None:
    rpc = FakeRPC({"eth_getBalance": {"ok": True, "result": "0x0", "endpoint": "drpc"}})
    out = await live_balances(EVM_ADDR, deps(rpc=rpc))
    assert out == {}  # 0x0 means "nothing to report", not an entry


@pytest.mark.asyncio
async def test_live_balances_is_empty_when_every_endpoint_fails() -> None:
    rpc = FakeRPC({"getBalance": {"ok": False, "error": "down", "endpoint": None}})
    assert await live_balances(SOL_ADDR, deps(rpc=rpc)) == {}


# ── wallet profile ────────────────────────────────────────────────
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
    d = deps(rpc=rpc, pg=FakePG(rows=[TAG]), duck=FakeDuck(records=[TX, TX]), token="tok")
    out = await wallet.profile(SOL_ADDR, d)
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
    # Risk flags come out in their historical order.
    assert out["risk_signals"] == ["high_token_count", "known_malicious"]


@pytest.mark.asyncio
async def test_profile_degrades_one_source_at_a_time() -> None:
    """A dead Postgres, dead archives and dead RPC still produce a profile."""
    out = await wallet.profile(SOL_ADDR, deps(pg=None, token="", duck=None))
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
async def test_risk_flags_zero_token_wallet() -> None:
    rpc = FakeRPC({"getTokenAccountsByOwner": {"ok": True, "result": {"value": []}}})
    flags = await risk_signals(SOL_ADDR, {"solana": {"sol": 1.0}}, [], deps(rpc=rpc))
    assert flags == ["no_tokens"]


@pytest.mark.asyncio
async def test_risk_ignores_non_malicious_labels() -> None:
    flags = await risk_signals(SOL_ADDR, {}, [{"label": "exchange"}], deps())
    assert flags == []


# ── archive history ───────────────────────────────────────────────
@pytest.mark.asyncio
async def test_history_is_skipped_without_a_token() -> None:
    duck = FakeDuck(records=[TX])
    assert await history.archive_history(SOL_ADDR, deps(duck=duck, token="")) == {}
    assert duck.statements == []  # no archive query was attempted


@pytest.mark.asyncio
async def test_history_summarises_matches() -> None:
    out = await history.archive_history(EVM_ADDR, deps(duck=FakeDuck(records=[TX]), token="tok"))
    assert out["total_matches"] == 1
    assert out["unique_counterparties"] == 1
    assert out["sample_tx"] == [TX]


@pytest.mark.asyncio
async def test_history_reports_an_empty_archive_as_empty() -> None:
    assert await history.archive_history(SOL_ADDR, deps(duck=FakeDuck(records=[]), token="t")) == {}


@pytest.mark.asyncio
async def test_history_reports_a_query_failure_with_a_hint() -> None:
    out = await history.archive_history(
        SOL_ADDR, deps(duck=FakeDuck(error=RuntimeError("no files")), token="t")
    )
    assert "no files" in out["error"]
    assert out["note"] == history.NO_ARCHIVES


@pytest.mark.asyncio
async def test_history_uses_the_solana_directory_for_a_solana_address() -> None:
    duck = FakeDuck(records=[TX])
    await history.archive_history(SOL_ADDR, deps(duck=duck, token="t"))
    assert "/solana/" in duck.statements[0][0]


@pytest.mark.xfail(
    strict=True,
    reason="pre-existing: chain_dir keys off len(address) <= 44, so every 0x EVM "
    "address (42 chars) is routed to the solana archive",
)
@pytest.mark.asyncio
async def test_history_routes_an_evm_address_to_the_evm_archives() -> None:
    """Known defect, pinned. Fixing the heuristic flips this to XPASS -> update it."""
    duck = FakeDuck(records=[TX])
    await history.archive_history(EVM_ADDR, deps(duck=duck, token="t"))
    assert "/evm/" in duck.statements[0][0]


# ── entity search ─────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_entity_search_without_postgres_says_so() -> None:
    out = await entity_tool.search_entities("bad", deps(pg=None))
    assert out == {"ok": False, "error": entity_tool.NO_POSTGRES}


@pytest.mark.asyncio
async def test_entity_search_returns_matches() -> None:
    pg = FakePG(rows=[TAG])
    out = await entity_tool.search_entities("bad", deps(pg=pg))
    assert out["ok"] is True and out["matches"] == 1
    assert out["results"][0]["address"] == EVM_ADDR
    assert out["results"][0]["entity"] == "Bad Corp"
    assert pg.closed is True


@pytest.mark.asyncio
async def test_entity_search_returns_a_typed_failure() -> None:
    out = await entity_tool.search_entities("bad", deps(pg=FakePG(error=RuntimeError("boom"))))
    assert out["ok"] is False and "boom" in out["error"]


# ── duckdb query + static schema ──────────────────────────────────
@pytest.mark.asyncio
async def test_duckdb_query_is_refused_without_a_token() -> None:
    out = await duck_tool.query_archives("SELECT 1", deps(token=""))
    assert out == {"ok": False, "error": duck_tool.NO_TOKEN}


@pytest.mark.asyncio
async def test_duckdb_query_truncates_at_the_row_cap() -> None:
    duck = FakeDuck(records=[{"n": i} for i in range(150)])
    out = await duck_tool.query_archives("SELECT * FROM t", deps(duck=duck, token="t"))
    assert out["rows"] == 150
    assert len(out["data"]) == duck_tool.MAX_ROWS
    assert out["truncated"] is True
    assert out["columns"] == ["n"]


@pytest.mark.asyncio
async def test_duckdb_query_reports_sql_errors() -> None:
    out = await duck_tool.query_archives(
        "SELECT bad", deps(duck=FakeDuck(error=ValueError("x")), token="t")
    )
    assert out["ok"] is False


@pytest.mark.asyncio
async def test_archive_schema_is_static_and_never_touches_the_network() -> None:
    out = await schema_tool.intel_archive_schema()
    assert out["ok"] is True
    assert set(out["chains"]) == {"solana", "evm"}
    assert out["storage"]["provider"] == "Hugging Face Hub"

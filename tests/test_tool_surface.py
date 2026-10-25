"""Tool-surface stability + envelope-conformance ratchet.

`tests/fixtures/tool_surface.json` pins what agents see from `tools/list`:
every tool name, description and input schema. Splitting modules must never
move it — regenerate deliberately with `scripts/tool_surface_snapshot.py
--update` and review the diff.

The ratchet below records which tools do NOT yet return the
{data, source, fetched_at, stale, age_s} envelope. The set may only shrink:
adding a tool that skips the envelope, or deleting one that honours it,
fails until the pin is updated on purpose.
"""

from __future__ import annotations

import json
import pathlib

import pytest

import data_mcp

PIN = pathlib.Path(__file__).parent / "fixtures" / "tool_surface.json"
PINNED = json.loads(PIN.read_text())

# The DEX lane honours the envelope contract today.
ENVELOPE_TOOLS = frozenset(
    {
        "dex_boosts",
        "dex_ohlcv",
        "dex_pairs",
        "dex_pools",
        "dex_profiles",
        "dex_search",
        "dex_token",
    }
)

# Legacy shapes ({ok, result, endpoint} / custom dicts) — ratchet, only shrinks.
LEGACY_TOOLS = frozenset(
    {
        "evm_blockNumber",
        "evm_call",
        "evm_getBalance",
        "evm_getBlock",
        "evm_getLogs",
        "evm_getTransaction",
        "evm_getTransactionReceipt",
        "evm_health",
        "intel_archive_schema",
        "intel_duckdb_query",
        "intel_entity_search",
        "intel_wallet_profile",
        "sol_getAccountInfo",
        "sol_getBalance",
        "sol_getBlock",
        "sol_getEpochInfo",
        "sol_getMultipleAccounts",
        "sol_getProgramAccounts",
        "sol_getRecentBlockhash",
        "sol_getSignaturesForAddress",
        "sol_getSlot",
        "sol_getTokenAccounts",
        "sol_getTokenLargestAccounts",
        "sol_getTokenPrice",
        "sol_getTokenSupply",
        "sol_getTransaction",
        "sol_health",
        "sol_simulateTransaction",
        "web_scrape",
        "xchain_health",
        "xchain_profile",
    }
)


async def _live() -> dict:
    tools = await data_mcp.mcp.list_tools()
    return {
        t.name: {"description": t.description or "", "input_schema": t.parameters} for t in tools
    }


@pytest.mark.asyncio
async def test_tool_names_are_exactly_the_pinned_set() -> None:
    live = await _live()
    assert sorted(live) == sorted(PINNED)
    assert len(live) == 38


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(PINNED))
async def test_tool_description_and_input_schema_are_pinned(name: str) -> None:
    live = await _live()
    assert live[name]["description"] == PINNED[name]["description"], f"{name} description drifted"
    assert live[name]["input_schema"] == PINNED[name]["input_schema"], f"{name} schema drifted"


@pytest.mark.asyncio
async def test_envelope_conformance_ratchet() -> None:
    """Every tool is either envelope-conforming or explicitly legacy."""
    live = set(await _live())
    assert live == ENVELOPE_TOOLS | LEGACY_TOOLS
    assert not ENVELOPE_TOOLS & LEGACY_TOOLS
    assert live - LEGACY_TOOLS == ENVELOPE_TOOLS


def test_every_documented_dex_tool_is_in_the_envelope_set() -> None:
    assert ENVELOPE_TOOLS == {n for n in PINNED if n.startswith("dex_")}

"""DexScreener tools: recorded fixtures replayed with respx (no network).

Covers parse, trimming, chain/address normalization in the outbound URL,
cache hits, typed provider-down errors, and non-empty-success guarantees.
"""

from __future__ import annotations

import json
import pathlib

import httpx
import pytest
import respx

import data_mcp_dex as dex
import data_mcp_dex_core as core

FIX = pathlib.Path(__file__).parent / "fixtures" / "dex"
BASE = "https://api.dexscreener.com"
SOL = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
EVM = "0xAbC0000000000000000000000000000000000001"


def fx(name: str) -> object:
    return json.loads((FIX / name).read_text())


@pytest.fixture(autouse=True)
def _clean() -> None:
    core.STORE.clear()
    for gate in core.GATES.values():
        gate.reset()


@pytest.mark.asyncio
@respx.mock
async def test_search_parses_and_trims() -> None:
    route = respx.get(url__startswith=f"{BASE}/latest/dex/search").mock(
        return_value=httpx.Response(200, json=fx("dexscreener_search.json"))
    )
    env = await dex.dex_search("bonk", limit=1)
    assert set(env) == {"data", "source", "fetched_at", "stale", "age_s"}
    assert env["source"] == "dexscreener" and env["stale"] is False
    assert len(env["data"]["pairs"]) == 1
    assert route.calls[0].request.url.params["q"] == "bonk"


@pytest.mark.asyncio
async def test_search_rejects_blank_query() -> None:
    with pytest.raises(ValueError):
        await dex.dex_search("   ")


@pytest.mark.asyncio
@respx.mock
async def test_pairs_normalizes_evm_address_in_url() -> None:
    route = respx.get(url__startswith=f"{BASE}/latest/dex/pairs/base/").mock(
        return_value=httpx.Response(200, json=fx("dexscreener_pairs.json"))
    )
    env = await dex.dex_pairs("BASE", EVM)
    assert env["data"]["pairs"]
    assert EVM.lower() in str(route.calls[0].request.url)


@pytest.mark.asyncio
@respx.mock
async def test_pairs_rejects_invalid_address() -> None:
    with pytest.raises(ValueError):
        await dex.dex_pairs("ethereum", "0xdeadbeef")


@pytest.mark.asyncio
@respx.mock
async def test_token_accepts_csv_and_caps_at_30() -> None:
    respx.get(url__startswith=f"{BASE}/latest/dex/tokens/").mock(
        return_value=httpx.Response(200, json=fx("dexscreener_token.json"))
    )
    env = await dex.dex_token(f"{SOL}, {SOL}", chain="solana")
    assert env["data"]["pairs"]
    with pytest.raises(ValueError):
        await dex.dex_token(",".join([SOL] * 31))


@pytest.mark.asyncio
@respx.mock
async def test_boosts_latest_and_top_paths() -> None:
    respx.get(f"{BASE}/token-boosts/latest/v1").mock(
        return_value=httpx.Response(200, json=fx("dexscreener_boosts_latest.json"))
    )
    respx.get(f"{BASE}/token-boosts/top/v1").mock(
        return_value=httpx.Response(200, json=fx("dexscreener_boosts_top.json"))
    )
    latest = await dex.dex_boosts("latest", limit=1)
    top = await dex.dex_boosts("top", limit=2)
    assert len(latest["data"]) == 1 and len(top["data"]) == 2
    with pytest.raises(ValueError):
        await dex.dex_boosts("weekly")


@pytest.mark.asyncio
@respx.mock
async def test_profiles_returns_list() -> None:
    respx.get(f"{BASE}/token-profiles/latest/v1").mock(
        return_value=httpx.Response(200, json=fx("dexscreener_profiles.json"))
    )
    env = await dex.dex_profiles(limit=2)
    assert isinstance(env["data"], list) and len(env["data"]) == 2


@pytest.mark.asyncio
@respx.mock
async def test_cache_hit_avoids_second_upstream_call() -> None:
    route = respx.get(url__startswith=f"{BASE}/latest/dex/search").mock(
        return_value=httpx.Response(200, json=fx("dexscreener_search.json"))
    )
    await dex.dex_search("bonk")
    await dex.dex_search("bonk")
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_upstream_error_raises_typed_error_not_empty_success() -> None:
    respx.get(url__startswith=f"{BASE}/latest/dex/search").mock(
        return_value=httpx.Response(404, json={"error": "nf"})
    )
    with pytest.raises(core.DataUnavailable) as info:
        await dex.dex_search("does-not-exist")
    assert info.value.source == "dexscreener" and info.value.status == 404


@pytest.mark.asyncio
@respx.mock
async def test_unexpected_schema_raises_typed_error() -> None:
    respx.get(url__startswith=f"{BASE}/latest/dex/search").mock(
        return_value=httpx.Response(200, json={"result": "weird"})
    )
    with pytest.raises(core.DataUnavailable) as info:
        await dex.dex_search("bonk")
    assert info.value.reason == "unexpected response schema"


def test_tools_are_registered_on_the_facade() -> None:
    import data_mcp

    for name in ("dex_search", "dex_pairs", "dex_token", "dex_boosts", "dex_profiles"):
        assert callable(getattr(data_mcp, name))

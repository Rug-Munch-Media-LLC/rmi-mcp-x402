"""GeckoTerminal tools: recorded fixtures replayed with respx (no network)."""

from __future__ import annotations

import json
import pathlib

import httpx
import pytest
import respx

import data_mcp_dex_core as core
import data_mcp_gecko as gecko

FIX = pathlib.Path(__file__).parent / "fixtures" / "dex"
API = "https://api.geckoterminal.com/api/v2"
SOL = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
POOL = "7Yk7UAsViEvP5wkyfapAFS7KySmCnJtpvamxmCMKiavU"


def fx(name: str) -> object:
    return json.loads((FIX / name).read_text())


@pytest.fixture(autouse=True)
def _clean() -> None:
    core.STORE.clear()
    for gate in core.GATES.values():
        gate.reset()


@pytest.mark.asyncio
@respx.mock
async def test_pools_network_list_trims_and_keeps_meta() -> None:
    respx.get(f"{API}/networks/solana/pools").mock(
        return_value=httpx.Response(200, json=fx("geckoterminal_pools.json"))
    )
    env = await gecko.dex_pools("solana", limit=1)
    assert env["source"] == "geckoterminal"
    assert len(env["data"]["pools"]) == 1
    assert isinstance(env["data"]["meta"], dict)


@pytest.mark.asyncio
@respx.mock
async def test_pools_token_path_and_alias() -> None:
    evm = "0xAbC0000000000000000000000000000000000001"
    route = respx.get(f"{API}/networks/eth/tokens/{evm.lower()}/pools").mock(
        return_value=httpx.Response(200, json=fx("geckoterminal_token_pools.json"))
    )
    env = await gecko.dex_pools("eth", token=evm)  # alias -> network id "eth"
    assert env["data"]["pools"]
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_pools_search_path() -> None:
    route = respx.get(f"{API}/search/pools").mock(
        return_value=httpx.Response(200, json=fx("geckoterminal_pools.json"))
    )
    await gecko.dex_pools("solana", query="bonk")
    req = route.calls[0].request
    assert req.url.params["query"] == "bonk"
    assert req.url.params["network"] == "solana"


@pytest.mark.asyncio
@respx.mock
async def test_pools_rejects_invalid_token() -> None:
    with pytest.raises(ValueError):
        await gecko.dex_pools("solana", token="0xnope")


@pytest.mark.asyncio
@respx.mock
async def test_ohlcv_returns_candles_and_params() -> None:
    route = respx.get(f"{API}/networks/solana/pools/{POOL}/ohlcv/hour").mock(
        return_value=httpx.Response(200, json=fx("geckoterminal_ohlcv.json"))
    )
    env = await gecko.dex_ohlcv("solana", POOL, resolution="hour", limit=5)
    assert len(env["data"]["ohlcv"]) >= 1
    assert len(env["data"]["ohlcv"][0]) == 6  # ts, o, h, l, c, v
    assert route.calls[0].request.url.params["aggregate"] == "1"


@pytest.mark.asyncio
async def test_ohlcv_rejects_bad_timeframe_and_aggregate() -> None:
    with pytest.raises(ValueError):
        await gecko.dex_ohlcv("solana", POOL, resolution="week")
    with pytest.raises(ValueError):
        await gecko.dex_ohlcv("solana", POOL, resolution="day:4")
    with pytest.raises(ValueError):
        await gecko.dex_ohlcv("solana", POOL, resolution="hour:x")


@pytest.mark.asyncio
@respx.mock
async def test_provider_down_raises_typed_error() -> None:
    respx.get(f"{API}/networks/solana/pools").mock(
        return_value=httpx.Response(404, json={"error": "nf"})
    )
    with pytest.raises(core.DataUnavailable) as info:
        await gecko.dex_pools("solana")
    assert info.value.source == "geckoterminal" and info.value.status == 404


@pytest.mark.asyncio
@respx.mock
async def test_pools_cache_hit() -> None:
    route = respx.get(f"{API}/networks/solana/pools").mock(
        return_value=httpx.Response(200, json=fx("geckoterminal_pools.json"))
    )
    await gecko.dex_pools("solana")
    await gecko.dex_pools("solana")
    assert route.call_count == 1

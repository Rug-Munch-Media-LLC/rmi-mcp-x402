"""The DEX envelope contract, proven for EVERY DexScreener/GeckoTerminal tool.

Per tool (7 of them, driven by recorded fixtures replayed with respx):

  1. Success returns exactly {data, source, fetched_at, stale, age_s} with
     `data` present (never None) and `stale` False — no empty success.
  2. A provider failure raises the typed `DataUnavailable` carrying source and
     status — never an empty envelope, never a bare exception.
  3. An upstream that legitimately returns zero rows is a success, not a
     failure.

Then, at the fetch layer (deterministic `Rig` clock/sleeper, no network):
fresh-hit TTL, refetch after TTL, stale serving inside the per-source window,
expiry of that window, and the reported age.

No network and no patching: fixtures + the injected seam only.
"""

from __future__ import annotations

import json
import pathlib
from typing import Callable

import httpx
import pytest
import respx
from dex_harness import Call, Client, Clock, Resp, Sleeps, rig

import data_mcp_dex as dex
import data_mcp_dex_core as core
import data_mcp_dex_http as dex_http
import data_mcp_gecko as gecko

FIX = pathlib.Path(__file__).parent / "fixtures" / "dex"
DS = "https://api.dexscreener.com"
GT = "https://api.geckoterminal.com/api/v2"
SOL = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
EVM = "0xAbC0000000000000000000000000000000000001"
POOL = "7Yk7UAsViEvP5wkyfapAFS7KySmCnJtpvamxmCMKiavU"

ENVELOPE = {"data", "source", "fetched_at", "stale", "age_s"}


def fx(name: str) -> object:
    return json.loads((FIX / name).read_text())


class Case:
    """One tool, its fixture-backed call, and the upstream URL it hits."""

    def __init__(
        self,
        name: str,
        call: Callable[[], object],
        prefix: str,
        fixture: str,
        source: str,
        empty: object,
    ) -> None:
        self.name, self.call, self.prefix, self.fixture, self.source = (
            name,
            call,
            prefix,
            fixture,
            source,
        )
        self.empty = empty


CASES = [
    Case(
        "dex_search",
        lambda: dex.dex_search("bonk", limit=2),
        f"{DS}/latest/dex/search",
        "dexscreener_search.json",
        "dexscreener",
        {"pairs": []},
    ),
    Case(
        "dex_pairs",
        lambda: dex.dex_pairs("base", EVM),
        f"{DS}/latest/dex/pairs/base/",
        "dexscreener_pairs.json",
        "dexscreener",
        {"pairs": []},
    ),
    Case(
        "dex_token",
        lambda: dex.dex_token(SOL, chain="solana"),
        f"{DS}/latest/dex/tokens/",
        "dexscreener_token.json",
        "dexscreener",
        {"pairs": []},
    ),
    Case(
        "dex_boosts",
        lambda: dex.dex_boosts("latest", limit=2),
        f"{DS}/token-boosts/latest/v1",
        "dexscreener_boosts_latest.json",
        "dexscreener",
        [],
    ),
    Case(
        "dex_profiles",
        lambda: dex.dex_profiles(limit=2),
        f"{DS}/token-profiles/latest/v1",
        "dexscreener_profiles.json",
        "dexscreener",
        [],
    ),
    Case(
        "dex_pools",
        lambda: gecko.dex_pools("solana", limit=2),
        f"{GT}/networks/solana/pools",
        "geckoterminal_pools.json",
        "geckoterminal",
        {"data": [], "meta": {}},
    ),
    Case(
        "dex_ohlcv",
        lambda: gecko.dex_ohlcv("solana", POOL, resolution="hour", limit=5),
        f"{GT}/networks/solana/pools/{POOL}/ohlcv/hour",
        "geckoterminal_ohlcv.json",
        "geckoterminal",
        {"data": {"attributes": {"ohlcv_list": []}}, "meta": {}},
    ),
]


@pytest.fixture(autouse=True)
def _clean() -> None:
    core.STORE.clear()
    for gate in core.GATES.values():
        gate.reset()


@pytest.fixture(params=CASES, ids=[c.name for c in CASES])
def case(request) -> Case:
    return request.param


@pytest.mark.asyncio
@respx.mock
async def test_tool_returns_the_envelope(case: Case) -> None:
    respx.get(url__startswith=case.prefix).mock(
        return_value=httpx.Response(200, json=fx(case.fixture))
    )
    env = await case.call()
    assert set(env) == ENVELOPE, case.name
    assert env["source"] == case.source
    assert env["stale"] is False
    assert env["data"] is not None, f"{case.name} returned an empty success"
    assert isinstance(env["fetched_at"], float) and env["fetched_at"] > 0
    assert env["age_s"] >= 0


@pytest.mark.asyncio
@respx.mock
async def test_tool_raises_typed_error_when_provider_is_down(case: Case) -> None:
    # 404 is non-retryable, so the failure surfaces on the first attempt.
    respx.get(url__startswith=case.prefix).mock(return_value=httpx.Response(404, json={}))
    with pytest.raises(core.DataUnavailable) as info:
        await case.call()
    assert info.value.source == case.source
    assert info.value.status == 404
    assert info.value.payload()["type"] == "DataUnavailable"


@pytest.mark.asyncio
@respx.mock
async def test_empty_upstream_payload_is_a_success(case: Case) -> None:
    """Zero rows is data; only a failed fetch may raise."""
    respx.get(url__startswith=case.prefix).mock(return_value=httpx.Response(200, json=case.empty))
    env = await case.call()
    assert env["stale"] is False
    assert env["data"] is not None


# ── fetch layer: TTL, stale window, pacing ────────────────────────
# Per-source contract from data_mcp_dex_core.SOURCES.
WINDOWS = [
    ("dexscreener", 30, 300),  # ttl, stale_ttl
    ("geckoterminal", 60, 900),
]


@pytest.fixture(params=WINDOWS, ids=[w[0] for w in WINDOWS])
def source_window(request) -> tuple[str, int, int]:
    return request.param


def _call(source: str) -> Call:
    path = "/latest/dex/search" if source == "dexscreener" else "/networks/solana/pools"
    return Call(source, path)


def _body(source: str) -> dict:
    return {"pairs": []} if source == "dexscreener" else {"data": [], "meta": {}}


@pytest.mark.asyncio
async def test_fresh_hit_within_ttl_skips_upstream(source_window) -> None:
    source, ttl, _ = source_window
    clock, sleeps = Clock(), Sleeps()
    client = Client(Resp(200, _body(source)))
    await dex_http.fetch(_call(source), rig=rig(client, clock, sleeps))
    clock.tick(ttl - 1)
    env = await dex_http.fetch(_call(source), rig=rig(client, clock, sleeps))
    assert len(client.calls) == 1
    assert env["stale"] is False
    assert env["age_s"] == ttl - 1


@pytest.mark.asyncio
async def test_refetch_after_ttl(source_window) -> None:
    source, ttl, _ = source_window
    clock, sleeps = Clock(), Sleeps()
    client = Client(Resp(200, _body(source)))
    await dex_http.fetch(_call(source), rig=rig(client, clock, sleeps))
    clock.tick(ttl + 1)
    await dex_http.fetch(_call(source), rig=rig(client, clock, sleeps))
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_stale_served_inside_the_stale_window(source_window) -> None:
    source, ttl, stale_ttl = source_window
    clock, sleeps = Clock(), Sleeps()
    ok = Client(Resp(200, _body(source)))
    first = await dex_http.fetch(_call(source), rig=rig(ok, clock, sleeps))
    clock.tick(ttl + 10)
    env = await dex_http.fetch(_call(source), rig=rig(Client(Resp(503, None)), clock, sleeps))
    assert env["stale"] is True
    assert env["data"] == first["data"]
    assert env["fetched_at"] == first["fetched_at"]  # original fetch time kept
    assert env["age_s"] == ttl + 10


@pytest.mark.asyncio
async def test_stale_window_expiry_raises(source_window) -> None:
    source, ttl, stale_ttl = source_window
    clock, sleeps = Clock(), Sleeps()
    await dex_http.fetch(_call(source), rig=rig(Client(Resp(200, _body(source))), clock, sleeps))
    clock.tick(stale_ttl + 1)
    with pytest.raises(core.DataUnavailable):
        await dex_http.fetch(_call(source), rig=rig(Client(Resp(503, None)), clock, sleeps))


@pytest.mark.asyncio
async def test_geckoterminal_min_interval_paces_calls() -> None:
    clock, sleeps = Clock(), Sleeps()
    client = Client(Resp(200, _body("geckoterminal")))
    for page in (1, 2):  # distinct calls, so the cache cannot answer the second
        await dex_http.fetch(
            Call("geckoterminal", "/networks/solana/pools", {"page": page}),
            rig=rig(client, clock, sleeps),
        )
    assert sleeps.waits[0] == 2.0  # SOURCES["geckoterminal"]["min_interval"]

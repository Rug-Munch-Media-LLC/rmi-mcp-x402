"""Cache TTL, stale serving and pacing — per provider, at the fetch layer.

Driven through the injected `Rig` clock/sleeper, so a 900-second stale window
costs no wall-clock time and no socket. Windows come from
data_mcp_dex_core.SOURCES; a provider that widens them must widen this test.
"""

from __future__ import annotations

import pytest
from dex_harness import Call, Client, Clock, Resp, Sleeps, rig

import data_mcp_dex_core as core
import data_mcp_dex_http as dex_http

# source, ttl, stale_ttl
WINDOWS = [
    ("dexscreener", 30, 300),
    ("geckoterminal", 60, 900),
]


@pytest.fixture(autouse=True)
def _clean() -> None:
    core.STORE.clear()
    for gate in core.GATES.values():
        gate.reset()


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
    source, ttl, _ = source_window
    clock, sleeps = Clock(), Sleeps()
    first = await dex_http.fetch(
        _call(source), rig=rig(Client(Resp(200, _body(source))), clock, sleeps)
    )
    clock.tick(ttl + 10)
    env = await dex_http.fetch(_call(source), rig=rig(Client(Resp(503, None)), clock, sleeps))
    assert env["stale"] is True
    assert env["data"] == first["data"]
    assert env["fetched_at"] == first["fetched_at"]  # original fetch time kept
    assert env["age_s"] == ttl + 10


@pytest.mark.asyncio
async def test_stale_window_expiry_raises(source_window) -> None:
    source, _, stale_ttl = source_window
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

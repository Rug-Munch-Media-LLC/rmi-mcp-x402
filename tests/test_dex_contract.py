"""Contract tests for the DEX lane: envelope, cache TTL/stale, rate gate,
typed DataUnavailable, backoff, schema validation.

No network: client, clock and sleeper are injected through `Rig` (DI seam —
the fleet never monkeypatches). Chain/address rules: test_dex_normalize.py.
"""

from __future__ import annotations

import httpx
import pytest

import data_mcp_dex_core as core
import data_mcp_dex_http as dex_http

Call, Rig = dex_http.Call, dex_http.Rig


class Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def tick(self, dt: float) -> None:
        self.t += dt


class Sleeps:
    def __init__(self) -> None:
        self.waits: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waits.append(round(seconds, 3))


class Resp:
    def __init__(self, status: int = 200, body: object = None, headers: dict | None = None) -> None:
        self.status_code = status
        self._body = body
        self.headers = headers or {}

    def json(self) -> object:
        return self._body


class Client:
    """httpx.AsyncClient-compatible double: one item per call."""

    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.calls: list[str] = []

    async def get(self, url: str, params: dict | None = None) -> Resp:
        self.calls.append(url)
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, Exception):
            raise item
        return item  # type: ignore[return-value]


def rig(client: Client, clock: Clock | None = None, sleeps: Sleeps | None = None) -> Rig:
    return Rig(client=client, clock=clock or Clock(), sleeper=sleeps or Sleeps())


@pytest.fixture(autouse=True)
def _clean() -> None:
    core.STORE.clear()
    for gate in core.GATES.values():
        gate.reset()


def test_envelope_contract() -> None:
    env = core.envelope("dexscreener", {"pairs": []}, 100.0, now=103.5)
    assert set(env) == {"data", "source", "fetched_at", "stale", "age_s"}
    assert env["source"] == "dexscreener"
    assert env["stale"] is False
    assert env["age_s"] == 3.5


def test_store_keeps_expired_entries_for_stale_serving() -> None:
    store = core.Store(mx=4)
    store.put("k", {"v": 1}, 100.0)
    store.put("a", 1, 1.0)
    store.put("b", 2, 2.0)
    assert store.peek("k") == ({"v": 1}, 100.0)  # no TTL logic in the store


def test_gate_window_and_min_interval() -> None:
    gate = core.Gate(per_sec=2, min_interval=0.0)
    assert gate.reserve(100.0) == 0.0
    assert gate.reserve(100.0) == 0.0
    assert gate.reserve(100.0) > 0  # third call in the same second waits
    gate.reset()
    paced = core.Gate(per_sec=10, min_interval=2.0)
    assert paced.reserve(200.0) == 0.0
    assert paced.reserve(200.0) == 2.0


@pytest.mark.asyncio
async def test_cache_ttl_short_circuits_upstream() -> None:
    clock, sleeps = Clock(), Sleeps()
    client = Client(Resp(200, {"pairs": []}))
    call = Call("dexscreener", "/latest/dex/search", {"q": "x"})
    await dex_http.fetch(call, rig=rig(client, clock, sleeps))
    await dex_http.fetch(call, rig=rig(client, clock, sleeps))
    assert len(client.calls) == 1
    clock.tick(31)  # past the 30s TTL
    await dex_http.fetch(call, rig=rig(client, clock, sleeps))
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_provider_down_serves_stale_not_empty() -> None:
    clock, sleeps = Clock(), Sleeps()
    call = Call("dexscreener", "/latest/dex/search", {"q": "x"})
    ok = Client(Resp(200, {"pairs": [{"pairAddress": "p"}]}))
    first = await dex_http.fetch(call, rig=rig(ok, clock, sleeps))
    assert first["stale"] is False

    clock.tick(120)
    env = await dex_http.fetch(call, rig=rig(Client(Resp(503, None)), clock, sleeps))
    assert env["stale"] is True
    assert env["data"] == {"pairs": [{"pairAddress": "p"}]}
    assert env["age_s"] == 120.0


@pytest.mark.asyncio
async def test_provider_down_without_cache_raises_typed_error() -> None:
    with pytest.raises(core.DataUnavailable) as info:
        await dex_http.fetch(
            Call("dexscreener", "/latest/dex/tokens/abc"),
            rig=rig(Client(Resp(503, None)), sleeps=Sleeps()),
        )
    assert info.value.source == "dexscreener"
    assert info.value.status == 503
    assert info.value.payload()["type"] == "DataUnavailable"


@pytest.mark.asyncio
async def test_transport_error_backs_off_and_never_returns_empty() -> None:
    sleeps = Sleeps()
    with pytest.raises(core.DataUnavailable):
        await dex_http.fetch(
            Call("dexscreener", "/latest/dex/search"),
            rig=rig(Client(httpx.ConnectError("boom")), sleeps=sleeps),
        )
    assert sleeps.waits == [0.5, 1.0]  # exponential backoff, 3 attempts total


@pytest.mark.asyncio
async def test_rate_limit_waits_before_sixth_call() -> None:
    clock, sleeps = Clock(), Sleeps()
    client = Client(Resp(200, {"pairs": []}))
    for i in range(6):
        await dex_http.fetch(Call("dexscreener", f"/p{i}"), rig=rig(client, clock, sleeps))
    assert sleeps.waits == [1.0]


@pytest.mark.asyncio
async def test_schema_validation_rejects_garbage() -> None:
    with pytest.raises(core.DataUnavailable) as info:
        await dex_http.fetch(
            Call("dexscreener", "/latest/dex/search", {"q": "x"}),
            validate=lambda body: isinstance(body.get("pairs"), list),
            rig=rig(Client(Resp(200, {"oops": True}))),
        )
    assert info.value.reason == "unexpected response schema"

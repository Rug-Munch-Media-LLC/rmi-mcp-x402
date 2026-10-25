"""The RPC engine every sol_*/evm_* tool sits on: cache, failover, cool-downs.

Transport is mocked with respx, so no socket is opened and nothing is patched.
Global limiter/cache state is reset per test; endpoint tables are built fresh
so a cool-down written by one test cannot leak into the next.
"""

from __future__ import annotations

import pytest
import respx
from httpx import ConnectError, Response

import data_mcp_cache as cache_mod
import data_mcp_duck as duck
import data_mcp_pg as pg_mod
import data_mcp_rpc as rpc

URL = "https://rpc.test"


def eps(*names: str, rate: int = 10) -> list[dict]:
    """Fresh endpoint table — cool-downs live on the dict, so never share one."""
    return [{"name": n, "url": f"{URL}/{n}", "rate": rate, "cooldown": 0} for n in names]


@pytest.fixture(autouse=True)
def _clean() -> None:
    cache_mod._cache.s.clear()
    rpc._rl.w.clear()


@pytest.mark.asyncio
@respx.mock
async def test_readonly_result_is_cached() -> None:
    route = respx.post(f"{URL}/cache-me").mock(return_value=Response(200, json={"result": "0x1"}))
    table = eps("cache-me")
    first = await rpc._rpc_call(table, "eth_blockNumber", [], ttl=30)
    second = await rpc._rpc_call(table, "eth_blockNumber", [], ttl=30)
    assert first == {"ok": True, "result": "0x1", "endpoint": "cache-me", "cached": False}
    assert second["endpoint"] == "cache" and second["cached"] is True
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_write_methods_are_never_cached() -> None:
    route = respx.post(f"{URL}/no-cache").mock(return_value=Response(200, json={"result": "sig"}))
    table = eps("no-cache")
    await rpc._rpc_call(table, "sendTransaction", ["tx"])
    await rpc._rpc_call(table, "sendTransaction", ["tx"])
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_429_cools_the_endpoint_down_and_fails_over() -> None:
    respx.post(f"{URL}/limited").mock(return_value=Response(429))
    respx.post(f"{URL}/backup").mock(return_value=Response(200, json={"result": "0x2"}))
    table = eps("limited", "backup")
    out = await rpc._rpc_call(table, "eth_chainId", [])
    assert out["ok"] is True and out["endpoint"] == "backup"
    assert table[0]["cooldown"] > 0


@pytest.mark.asyncio
@respx.mock
async def test_jsonrpc_error_fails_over_to_the_next_endpoint() -> None:
    respx.post(f"{URL}/bad").mock(return_value=Response(200, json={"error": {"message": "boom"}}))
    respx.post(f"{URL}/good").mock(return_value=Response(200, json={"result": 7}))
    out = await rpc._rpc_call(eps("bad", "good"), "eth_blockNumber", [])
    assert out == {"ok": True, "result": 7, "endpoint": "good", "cached": False}


@pytest.mark.asyncio
@respx.mock
async def test_all_endpoints_down_returns_error_envelope() -> None:
    respx.post(f"{URL}/d1").mock(return_value=Response(500, json={}))
    respx.post(f"{URL}/d2").mock(side_effect=ConnectError("nope"))
    out = await rpc._rpc_call(eps("d1", "d2"), "eth_blockNumber", [])
    assert out["ok"] is False
    assert out["endpoint"] is None
    assert out["error"]


@pytest.mark.asyncio
@respx.mock
async def test_non_rpc_5xx_is_never_reported_as_success() -> None:
    """Regression: a gateway's HTML/empty error page used to return result None."""
    respx.post(f"{URL}/page").mock(return_value=Response(502, json={"message": "bad gateway"}))
    out = await rpc._rpc_call(eps("page"), "eth_chainId", [])
    assert out["ok"] is False
    assert "malformed" in out["error"]


def test_healthy_clears_cool_downs_when_everything_is_cooling() -> None:
    table = eps("c1", "c2")
    for ep in table:
        ep["cooldown"] = 10**9  # far in the future
    picked = rpc._healthy(table)
    assert [ep["name"] for ep in picked] == ["c1", "c2"]
    assert all(ep["cooldown"] == 0 for ep in table)


class Recorder:
    """Stands in for a DuckDB connection: records the statements it is given."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, sql: str) -> None:
        self.statements.append(sql)


def test_httpfs_init_loads_extension_and_token_secret() -> None:
    db = Recorder()
    duck._init_httpfs(db, token="tok-123")
    assert db.statements[0] == duck.HTTPFS
    assert "Bearer tok-123" in db.statements[1]


def test_httpfs_init_without_token_skips_the_secret() -> None:
    db = Recorder()
    duck._init_httpfs(db, token="")
    assert db.statements == [duck.HTTPFS]


@pytest.mark.asyncio
async def test_postgres_handle_is_none_without_credentials() -> None:
    if pg_mod.PG_PASS:
        pytest.skip("POSTGRES_PASSWORD is set in this environment")
    assert await pg_mod._get_pg() is None

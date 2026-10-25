"""The RPC layer every sol_*/evm_* tool sits on — limiter, cache, failover.

Transport is mocked with respx, so no socket is opened and nothing is patched.
Global limiter/cache state is reset per test; endpoint tables are built fresh
so a cooldown written by one test cannot leak into the next.
"""

from __future__ import annotations

import pytest
import respx
from httpx import ConnectError, Response

import data_mcp_cache as cache_mod
import data_mcp_duck as duck
import data_mcp_pg as pg_mod
import data_mcp_rpc as rpc
from data_mcp_cache import Cache
from data_mcp_rl import RL

URL = "https://rpc.test"


def eps(*names: str, rate: int = 10) -> list[dict]:
    """Fresh endpoint table — cooldowns are per-dict, so never share one."""
    return [{"name": n, "url": f"{URL}/{n}", "rate": rate, "cooldown": 0} for n in names]


@pytest.fixture(autouse=True)
def _clean() -> None:
    cache_mod._cache.s.clear()
    rpc._rl.w.clear()


# ── rate limiter ──────────────────────────────────────────────────
def test_rate_limiter_allows_the_cap_then_refuses() -> None:
    rl = RL()
    assert [rl.ok("ep-test-a", 2) for _ in range(3)] == [True, True, False]


def test_rate_limiter_is_per_endpoint() -> None:
    rl = RL()
    assert rl.ok("ep-test-b", 1) is True
    assert rl.ok("ep-test-c", 1) is True


# ── cache ─────────────────────────────────────────────────────────
def test_cache_roundtrip_and_key_isolation() -> None:
    c = Cache()
    c.set("eth_blockNumber", [], {"n": 1})
    assert c.get("eth_blockNumber", []) == {"n": 1}
    assert c.get("eth_blockNumber", ["other"]) is None


def test_cache_entry_expires_at_ttl() -> None:
    c = Cache()
    c.set("m", None, "v", ttl=0)  # already expired on write
    assert c.get("m", None) is None


def test_cache_evicts_oldest_at_capacity() -> None:
    c = Cache(mx=2)
    c.set("m", ["a"], 1)
    c.set("m", ["b"], 2)
    c.set("m", ["c"], 3)
    assert len(c.s) == 2
    assert c.get("m", ["a"]) is None


# ── rpc_call ──────────────────────────────────────────────────────
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
async def test_jsonrpc_error_fails_over_and_reports_last_error() -> None:
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


def test_healthy_clears_cooldowns_when_everything_is_cooling() -> None:
    table = eps("c1", "c2")
    for ep in table:
        ep["cooldown"] = 10**9  # far in the future
    picked = rpc._healthy(table)
    assert [ep["name"] for ep in picked] == ["c1", "c2"]
    assert all(ep["cooldown"] == 0 for ep in table)


# ── duckdb / postgres handles ─────────────────────────────────────
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

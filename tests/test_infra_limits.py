"""The two in-memory guards under the RPC engine: rate limiter and TTL cache."""

from __future__ import annotations

import data_mcp_cache as cache_mod
from data_mcp_cache import Cache
from data_mcp_rl import RL


def test_rate_limiter_allows_the_cap_then_refuses() -> None:
    rl = RL()
    assert [rl.ok("ep-test-a", 2) for _ in range(3)] == [True, True, False]


def test_rate_limiter_is_per_endpoint() -> None:
    rl = RL()
    assert rl.ok("ep-test-b", 1) is True
    assert rl.ok("ep-test-c", 1) is True


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


def test_module_cache_is_the_shared_instance() -> None:
    cache_mod._cache.s.clear()
    cache_mod._cache.set("m", None, "v")
    assert cache_mod._cache.get("m", None) == "v"
    cache_mod._cache.s.clear()

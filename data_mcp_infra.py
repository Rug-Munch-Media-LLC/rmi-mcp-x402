#!/usr/bin/env python3
"""
RugMunch Data MCP — infrastructure layer.

Rate limiter, TTL cache, multi-endpoint RPC engine with failover,
lazy DuckDB (httpfs) and asyncpg connections.
"""

import hashlib
import json
import time
from collections import OrderedDict
from typing import Any, Optional

import asyncpg
import httpx

from data_mcp_config import (
    HF_HUB_TOKEN,
    PG_DB,
    PG_HOST,
    PG_PASS,
    PG_PORT,
    PG_USER,
)


# ── Rate Limiter ──────────────────────────────────────────────────
class RL:
    def __init__(self):
        self.w: dict[str, list[float]] = {}

    def ok(self, ep: str, mx: float) -> bool:
        n = time.monotonic()
        w = [t for t in self.w.get(ep, []) if n - t < 1.0]
        self.w[ep] = w
        if len(w) >= mx:
            return False
        w.append(n)
        return True


_rl = RL()


# ── Cache ─────────────────────────────────────────────────────────
class Cache:
    def __init__(self, mx=500):
        self.s: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self.mx = mx

    def _k(self, m: str, p: Any) -> str:
        return hashlib.sha256(f"{m}:{json.dumps(p, sort_keys=True)}".encode()).hexdigest()[:16]

    def get(self, m: str, p: Any, ttl: int = 30) -> Optional[Any]:
        k = self._k(m, p)
        if k in self.s:
            v, e = self.s[k]
            if time.time() < e:
                self.s.move_to_end(k)
                return v
            del self.s[k]
        return None

    def set(self, m: str, p: Any, v: Any, ttl: int = 30):
        k = self._k(m, p)
        if k in self.s:
            self.s.move_to_end(k)
        else:
            if len(self.s) >= self.mx:
                self.s.popitem(last=False)
        self.s[k] = (v, time.time() + ttl)


_cache = Cache()


# ── RPC Engine ────────────────────────────────────────────────────
def _healthy(endpoints):
    now = time.monotonic()
    out = []
    for ep in endpoints:
        if now < ep["cooldown"]:
            continue
        if _rl.ok(ep["name"], ep["rate"]):
            out.append(ep)
    if not out:
        for ep in endpoints:
            ep["cooldown"] = 0
        out = [e for e in endpoints]
    return out


async def _rpc_call(endpoints, method: str, params: list = None, ttl: int = 10) -> dict:
    readonly = {
        "getTransaction",
        "getAccountInfo",
        "getBalance",
        "getTokenAccountsByOwner",
        "getBlock",
        "getSignaturesForAddress",
        "getMultipleAccounts",
        "getProgramAccounts",
        "getTokenLargestAccounts",
        "getTokenSupply",
        "getEpochInfo",
        "getSlot",
        "getHealth",
        "eth_getBlockByNumber",
        "eth_getTransactionByHash",
        "eth_getBalance",
        "eth_getBlockByHash",
        "eth_call",
        "eth_getTransactionReceipt",
        "eth_getLogs",
        "eth_blockNumber",
        "eth_chainId",
    }
    if method in readonly:
        c = _cache.get(method, params, ttl)
        if c is not None:
            return {"ok": True, "result": c, "endpoint": "cache", "cached": True}
    eps = _healthy(endpoints)
    if not eps:
        return {"ok": False, "error": "No healthy endpoints", "endpoint": None}
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}
    lerr = None
    async with httpx.AsyncClient(timeout=15.0) as cl:
        for ep in eps:
            try:
                r = await cl.post(ep["url"], json=body)
                if r.status_code == 429:
                    ep["cooldown"] = time.monotonic() + 30
                    continue
                d = r.json()
                if "error" in d:
                    lerr = d["error"].get("message", str(d))
                    ep["cooldown"] = time.monotonic() + 5
                    continue
                result = d.get("result")
                if method in readonly and result is not None:
                    _cache.set(method, params, result, ttl)
                return {"ok": True, "result": result, "endpoint": ep["name"], "cached": False}
            except Exception as e:
                lerr = str(e)
                ep["cooldown"] = time.monotonic() + 10
                continue
    return {"ok": False, "error": lerr or "All failed", "endpoint": None}


# ── DuckDB httpfs initialization ─────────────────────────────────
_duck_initialized = False


def _get_duckdb():
    """Lazy-init DuckDB with httpfs for HF Hub remote queries."""
    global _duck_initialized
    import duckdb

    db = duckdb.connect()
    if not _duck_initialized:
        db.execute("INSTALL httpfs; LOAD httpfs;")
        if HF_HUB_TOKEN:
            db.execute(f"""
                CREATE SECRET hf_token (
                    TYPE HTTP,
                    EXTRA_HTTP_HEADERS MAP {{
                        'Authorization': 'Bearer {HF_HUB_TOKEN}'
                    }}
                );
            """)
        _duck_initialized = True
    return db


async def _get_pg():
    """Lazy-init asyncpg connection pool."""
    if not PG_PASS:
        return None
    try:
        conn = await asyncpg.connect(
            host=PG_HOST, port=PG_PORT, database=PG_DB, user=PG_USER, password=PG_PASS, timeout=10
        )
        return conn
    except Exception:
        return None

#!/usr/bin/env python3
"""
RugMunch Data MCP — multi-endpoint JSON-RPC engine.

One responsibility: turn (endpoints, method, params) into one result by
gating on rate limits, failing over across endpoints, cooling down the ones
that misbehave, and caching idempotent reads.
"""

import time

import httpx

from data_mcp_cache import _cache
from data_mcp_rl import _rl

# RPC methods whose result is safe to cache for `ttl` seconds.
READONLY = frozenset(
    {
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
)


def _healthy(endpoints):
    """Endpoints not in cooldown and under their per-second rate."""
    now = time.monotonic()
    out = []
    for ep in endpoints:
        if now < ep["cooldown"]:
            continue
        if _rl.ok(ep["name"], ep["rate"]):
            out.append(ep)
    if not out:
        # Nothing passed the gate: clear cooldowns and try everything rather
        # than failing a call the upstream might still serve.
        for ep in endpoints:
            ep["cooldown"] = 0
        out = [e for e in endpoints]
    return out


async def _rpc_call(endpoints, method: str, params: list = None, ttl: int = 10) -> dict:
    """Call `method` across `endpoints` with cache, failover and cooldowns."""
    if method in READONLY:
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
                if not isinstance(d, dict) or ("result" not in d and "error" not in d):
                    # Not a JSON-RPC answer (gateway error page, empty body).
                    # Never report this as success.
                    lerr = f"malformed response (http {r.status_code})"
                    ep["cooldown"] = time.monotonic() + 10
                    continue
                if "error" in d:
                    lerr = d["error"].get("message", str(d))
                    ep["cooldown"] = time.monotonic() + 5
                    continue
                result = d.get("result")
                if method in READONLY and result is not None:
                    _cache.set(method, params, result, ttl)
                return {"ok": True, "result": result, "endpoint": ep["name"], "cached": False}
            except Exception as e:
                lerr = str(e)
                ep["cooldown"] = time.monotonic() + 10
                continue
    return {"ok": False, "error": lerr or "All failed", "endpoint": None}

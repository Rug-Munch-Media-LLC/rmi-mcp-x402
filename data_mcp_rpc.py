#!/usr/bin/env python3
"""
RugMunch Data MCP — multi-endpoint JSON-RPC engine.

One responsibility: turn (endpoints, method, params) into one result by
gating on rate limits, failing over across endpoints, cooling down the ones
that misbehave, and caching idempotent reads.
"""

import time
from typing import Any

import httpx

from data_mcp_cache import _cache
from data_mcp_rl import _rl

# Cool-down windows, in seconds, per failure class.
COOLDOWN_429 = 30.0
COOLDOWN_ERROR = 5.0
COOLDOWN_TRANSPORT = 10.0

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


def _cached(method: str, params: Any, ttl: int) -> Any:
    """Cached result for a READONLY method, else None (reads are idempotent)."""
    if method not in READONLY:
        return None
    return _cache.get(method, params, ttl)


def _rpc_body(method: str, params: Any) -> dict:
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}


def _parse(body: Any, status: int) -> tuple[Any, Any]:
    """(result, error) from a JSON-RPC body. A non-RPC body is an error."""
    if not isinstance(body, dict) or ("result" not in body and "error" not in body):
        return None, f"malformed response (http {status})"
    if "error" in body:
        return None, body["error"].get("message", str(body))
    return body.get("result"), None


async def _attempt(client, ep: dict, body: dict) -> tuple[Any, Any]:
    """One endpoint attempt; cools the endpoint down when it misbehaves."""
    resp = await client.post(ep["url"], json=body)
    if resp.status_code == 429:
        ep["cooldown"] = time.monotonic() + COOLDOWN_429
        return None, "rate limited by endpoint"
    result, err = _parse(resp.json(), resp.status_code)
    if err is not None:
        ep["cooldown"] = time.monotonic() + COOLDOWN_ERROR
    return result, err


async def _first_answer(client, endpoints: list, body: dict) -> tuple[Any, Any, Any]:
    """Try each endpoint in turn -> (result, serving endpoint, last error)."""
    last = None
    for ep in endpoints:
        try:
            result, err = await _attempt(client, ep, body)
        except Exception as exc:  # transport failure or undecodable body
            ep["cooldown"] = time.monotonic() + COOLDOWN_TRANSPORT
            last = str(exc)
            continue
        if err is None:
            return result, ep, None
        last = err
    return None, None, last


async def _rpc_call(endpoints, method: str, params: list = None, ttl: int = 10) -> dict:
    """Call `method` across `endpoints` with cache, failover and cooldowns."""
    hit = _cached(method, params, ttl)
    if hit is not None:
        return {"ok": True, "result": hit, "endpoint": "cache", "cached": True}
    eps = _healthy(endpoints)
    if not eps:
        return {"ok": False, "error": "No healthy endpoints", "endpoint": None}
    async with httpx.AsyncClient(timeout=15.0) as client:
        result, ep, err = await _first_answer(client, eps, _rpc_body(method, params))
    if ep is None:
        return {"ok": False, "error": err or "All failed", "endpoint": None}
    if method in READONLY and result is not None:
        _cache.set(method, params, result, ttl)
    return {"ok": True, "result": result, "endpoint": ep["name"], "cached": False}

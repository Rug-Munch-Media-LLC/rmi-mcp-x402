#!/usr/bin/env python3
"""
RugMunch Data MCP — one HTTP path for every DEX provider call.

Rate gate -> request -> schema validation -> cache. Fresh cache hit returns
immediately; a failed upstream falls back to last-known-good (stale=True) and
otherwise raises the typed DataUnavailable. Never an empty success.
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

import httpx

from data_mcp_dex_core import (
    GATES,
    SOURCES,
    STORE,
    DataUnavailable,
    cache_key,
    envelope,
)

MAX_ATTEMPTS = 3
TIMEOUT = 10.0


@dataclass(frozen=True)
class Call:
    """One provider resource: source id, path, query params."""

    source: str
    path: str
    params: Optional[dict] = None


@dataclass
class Rig:
    """Injection seam — tests pass fakes, production uses the defaults."""

    client: Any = None
    clock: Callable[[], float] = time.time
    sleeper: Any = None


def _reason(status: int) -> str:
    if status == 429:
        return "rate limited by upstream"
    if status == 404:
        return "not found upstream"
    if status >= 500:
        return f"upstream {status}"
    return f"http {status}"


def _retry_after(headers: Any) -> float:
    try:
        return max(0.0, float(headers.get("Retry-After") or 0))
    except (TypeError, ValueError):
        return 0.0


async def _once(url: str, params: Optional[dict], client: Any) -> tuple[int, Any, float]:
    """Single request. An injected client is never closed; an owned one is."""
    owned = client is None
    cl = httpx.AsyncClient(timeout=TIMEOUT) if owned else client
    try:
        resp = await cl.get(url, params=params)
        body = resp.json() if resp.status_code == 200 else None
        return resp.status_code, body, _retry_after(resp.headers)
    finally:
        if owned:
            await cl.aclose()


async def _attempts(call: Call, rig: Rig) -> Any:
    """Up to MAX_ATTEMPTS with gate waits, Retry-After and exponential backoff."""
    url = SOURCES[call.source]["base"] + call.path
    sleep = asyncio.sleep if rig.sleeper is None else rig.sleeper
    delay = 0.5
    failure = DataUnavailable(call.source, "upstream error")
    for attempt in range(MAX_ATTEMPTS):
        wait = GATES[call.source].reserve(rig.clock())
        if wait:
            await sleep(wait)
        retry_after = 0.0
        try:
            status, body, retry_after = await _once(url, call.params, rig.client)
        except (httpx.HTTPError, ValueError) as exc:
            failure = DataUnavailable(call.source, f"transport: {exc.__class__.__name__}")
            status, body = 0, None
        if body is not None and isinstance(body, (dict, list)):
            return body
        if status:
            failure = DataUnavailable(call.source, _reason(status), status)
            if status in (400, 401, 403, 404):
                break  # not retryable: the request itself is wrong
        if attempt < MAX_ATTEMPTS - 1:
            await sleep(max(retry_after, delay))
        delay *= 2
    raise failure


async def fetch(
    call: Call,
    ttl: Optional[int] = None,
    *,
    validate: Optional[Callable[[Any], bool]] = None,
    rig: Optional[Rig] = None,
) -> dict:
    """Fetch one DEX resource -> {data, source, fetched_at, stale, age_s}.

    Raises:
        DataUnavailable: upstream failed and no last-known-good value exists.
    """
    cfg = SOURCES[call.source]
    ttl = cfg["ttl"] if ttl is None else ttl
    rig = Rig() if rig is None else rig
    key = cache_key(call.source, call.path, call.params)
    now = rig.clock()
    hit = STORE.peek(key)
    if hit is not None and now - hit[1] < ttl:
        return envelope(call.source, hit[0], hit[1], now=now)
    try:
        data = await _attempts(call, rig)
        if validate is not None and not validate(data):
            raise DataUnavailable(call.source, "unexpected response schema")
    except DataUnavailable as exc:
        if hit is not None and now - hit[1] <= cfg["stale_ttl"]:
            return envelope(call.source, hit[0], hit[1], stale=True, now=now)
        raise exc
    STORE.put(key, data, rig.clock())
    return envelope(call.source, data, rig.clock(), now=rig.clock())

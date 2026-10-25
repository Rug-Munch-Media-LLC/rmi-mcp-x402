#!/usr/bin/env python3
"""
RugMunch Data MCP — GeckoTerminal DEX tools.

dex_pools (network pools, token pools, or pool search) and dex_ohlcv
(candles per pool). Same envelope + typed DataUnavailable as the DexScreener
tools; this module only owns GeckoTerminal path/param/validation semantics.
"""

from typing import Any, Optional

from data_mcp_dex_chains import normalize_address, normalize_pair_id, provider_chain
from data_mcp_dex_http import Call, fetch
from data_mcp_server import mcp

# timeframe -> allowed aggregate values (GeckoTerminal rule)
AGGREGATES = {"minute": (1, 5, 15), "hour": (1, 4, 12), "day": (1,)}
MAX_LIMIT = 1000


def _is_pools(body: Any) -> bool:
    return isinstance(body, dict) and isinstance(body.get("data"), list)


def _is_ohlcv(body: Any) -> bool:
    return (
        isinstance(body, dict)
        and isinstance(body.get("data"), dict)
        and isinstance(body["data"].get("attributes"), dict)
    )


@mcp.tool()
async def dex_pools(
    network: str = "solana",
    token: str = "",
    query: str = "",
    page: int = 1,
    limit: int = 20,
) -> dict:
    """GeckoTerminal pools: all pools on a network, one token's pools, or a search.

    Args:
        network: Chain name or alias (solana, eth, bsc, base, arbitrum...).
        token: Token address — returns that token's pools (takes precedence).
        query: Free-text pool search (used only when token is empty).
        page: Page number, 1-based.
        limit: Max pools returned (1-100, default 20).

    Returns:
        {data: {pools: [...], meta: {...}}, source: "geckoterminal", fetched_at, stale, age_s}
    """
    net = provider_chain(network, "geckoterminal")
    pg = max(1, int(page))
    cap = max(1, min(int(limit), 100))
    if token.strip():
        path = f"/networks/{net}/tokens/{normalize_address(network, token)}/pools"
        params: Optional[dict] = {"page": pg}
    elif query.strip():
        path = "/search/pools"
        params = {"query": query.strip(), "network": net, "page": pg}
    else:
        path = f"/networks/{net}/pools"
        params = {"page": pg}
    env = await fetch(Call("geckoterminal", path, params), ttl=60, validate=_is_pools)
    body = env["data"]
    env["data"] = {"pools": body["data"][:cap], "meta": body.get("meta") or {}}
    return env


@mcp.tool()
async def dex_ohlcv(
    network: str,
    pool: str,
    resolution: str = "hour",
    limit: int = 100,
    before_timestamp: int = 0,
) -> dict:
    """OHLCV candles for one GeckoTerminal pool.

    Args:
        network: Chain name or alias (solana, eth, bsc, base, arbitrum...).
        pool: Pool address (the GeckoTerminal pool/pair id for that network).
        resolution: "<timeframe>" or "<timeframe>:<aggregate>" — timeframe is
            minute/hour/day; aggregates minute 1/5/15, hour 1/4/12, day 1.
            Examples: "hour", "hour:4", "minute:15".
        limit: Max candles (1-1000, default 100).
        before_timestamp: Unix seconds; return candles before this time (0 = latest).

    Returns:
        {data: {ohlcv: [[ts, o, h, l, c, v], ...], meta: {...}},
         source: "geckoterminal", fetched_at, stale, age_s}
    """
    net = provider_chain(network, "geckoterminal")
    pid = normalize_pair_id(network, pool)
    base, _, raw_agg = (resolution or "").partition(":")
    tf = base.strip().lower()
    if tf not in AGGREGATES:
        raise ValueError("resolution timeframe must be minute, hour, or day")
    try:
        agg = int(raw_agg) if raw_agg.strip() else 1
    except ValueError:
        raise ValueError("resolution aggregate must be an integer") from None
    if agg not in AGGREGATES[tf]:
        raise ValueError(f"aggregate for {tf} must be one of {AGGREGATES[tf]}")
    params: dict = {"aggregate": agg, "limit": max(1, min(int(limit), MAX_LIMIT))}
    if before_timestamp:
        params["before_timestamp"] = int(before_timestamp)
    env = await fetch(
        Call("geckoterminal", f"/networks/{net}/pools/{pid}/ohlcv/{tf}", params),
        ttl=60,
        validate=_is_ohlcv,
    )
    data = env["data"]["data"]["attributes"]
    env["data"] = {"ohlcv": data.get("ohlcv_list") or [], "meta": env["data"].get("meta") or {}}
    return env

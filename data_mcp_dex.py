#!/usr/bin/env python3
"""
RugMunch Data MCP — DexScreener market tools.

dex_search / dex_pairs / dex_token / dex_boosts / dex_profiles. Every tool
returns the shared envelope; provider failure raises DataUnavailable.
"""

from typing import Any

from data_mcp_dex_chains import normalize_address, normalize_pair_id, provider_chain
from data_mcp_dex_http import Call, fetch
from data_mcp_server import mcp

MAX_LIMIT = 50


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), MAX_LIMIT))


def _is_pairs(body: Any) -> bool:
    return isinstance(body, dict) and isinstance(body.get("pairs"), list)


def _is_list(body: Any) -> bool:
    return isinstance(body, list)


@mcp.tool()
async def dex_search(query: str, limit: int = 20) -> dict:
    """Search DexScreener for pairs by token symbol, name, or pair address.

    Args:
        query: Free-text search term (e.g. "bonk", "WIF/SOL").
        limit: Max pairs returned (1-50, default 20).

    Returns:
        {data: {pairs: [...]}, source: "dexscreener", fetched_at, stale, age_s}
    """
    q = (query or "").strip()
    if not q:
        raise ValueError("query is required")
    env = await fetch(
        Call("dexscreener", "/latest/dex/search", {"q": q}), ttl=30, validate=_is_pairs
    )
    env["data"] = {"pairs": env["data"]["pairs"][: _clamp(limit)]}
    return env


@mcp.tool()
async def dex_pairs(chain: str, pair: str) -> dict:
    """Look up one DexScreener pair by chain and pair address.

    Args:
        chain: Chain name or alias (solana, eth, bsc, base, arbitrum, polygon...).
        pair: Pair/pool address on that chain.

    Returns:
        {data: {pairs: [...]}, source: "dexscreener", fetched_at, stale, age_s}
    """
    net = provider_chain(chain, "dexscreener")
    pid = normalize_pair_id(chain, pair)
    return await fetch(
        Call("dexscreener", f"/latest/dex/pairs/{net}/{pid}"), ttl=30, validate=_is_pairs
    )


@mcp.tool()
async def dex_token(address: str, chain: str = "") -> dict:
    """All DexScreener pairs for one or more token addresses (comma-separated, max 30).

    Args:
        address: Token address, or comma-separated addresses (max 30).
        chain: Optional chain for address normalization/validation.

    Returns:
        {data: {pairs: [...]}, source: "dexscreener", fetched_at, stale, age_s}
    """
    raw = [a.strip() for a in (address or "").split(",") if a.strip()]
    if not raw:
        raise ValueError("address is required")
    if len(raw) > 30:
        raise ValueError("at most 30 token addresses per call")
    if chain:
        raw = [normalize_address(chain, a) for a in raw]
    return await fetch(
        Call("dexscreener", f"/latest/dex/tokens/{','.join(raw)}"),
        ttl=30,
        validate=_is_pairs,
    )


@mcp.tool()
async def dex_boosts(kind: str = "latest", limit: int = 20) -> dict:
    """DexScreener token boosts (paid promotion signal) — "latest" or "top".

    Args:
        kind: "latest" (most recent) or "top" (highest boost totals).
        limit: Max entries returned (1-50, default 20).

    Returns:
        {data: [...], source: "dexscreener", fetched_at, stale, age_s}
    """
    variant = (kind or "latest").strip().lower()
    if variant not in ("latest", "top"):
        raise ValueError("kind must be 'latest' or 'top'")
    env = await fetch(Call("dexscreener", f"/token-boosts/{variant}/v1"), ttl=60, validate=_is_list)
    env["data"] = env["data"][: _clamp(limit)]
    return env


@mcp.tool()
async def dex_profiles(limit: int = 20) -> dict:
    """DexScreener token profiles (team-submitted metadata) — latest 100.

    Args:
        limit: Max profiles returned (1-50, default 20).

    Returns:
        {data: [...], source: "dexscreener", fetched_at, stale, age_s}
    """
    env = await fetch(Call("dexscreener", "/token-profiles/latest/v1"), ttl=60, validate=_is_list)
    env["data"] = env["data"][: _clamp(limit)]
    return env

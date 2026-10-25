#!/usr/bin/env python3
"""
RugMunch Data MCP — intel_wallet_profile, the primary intelligence tool.

One responsibility: compose the wallet profile from the four independent
sources (balances, labels, archive history, risk signals).
"""

import time

from data_mcp_intel_balances import live_balances
from data_mcp_intel_deps import IntelDeps
from data_mcp_intel_history import archive_history
from data_mcp_intel_labels import entity_tags
from data_mcp_intel_risk import risk_signals
from data_mcp_server import mcp


async def profile(address: str, deps: IntelDeps) -> dict:
    """Build one wallet profile from the four sources, in contract order."""
    queried_at = time.time()
    balances = await live_balances(address, deps)
    tags = await entity_tags(address, deps)
    history = await archive_history(address, deps)
    return {
        "address": address,
        "queried_at": queried_at,
        "live_balances": balances,
        "entity_tags": tags,
        "historical_patterns": history,
        "risk_signals": await risk_signals(address, balances, tags, deps),
    }


@mcp.tool()
async def intel_wallet_profile(address: str) -> dict:
    """
    Comprehensive cross-chain wallet intelligence profile.
    Queries live RPCs (Solana + all EVM chains) + Postgres entity tags + DuckDB historical archives.
    This is the primary intelligence tool — combines live data with historical patterns and entity labels.
    """
    return await profile(address, IntelDeps())

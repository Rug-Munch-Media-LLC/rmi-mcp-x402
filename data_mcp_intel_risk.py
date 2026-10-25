#!/usr/bin/env python3
"""
RugMunch Data MCP — risk signals for the wallet profile.

One responsibility: turn a wallet's balances and labels into the short list
of risk flags the profile reports.
"""

from data_mcp_config import SOL_RPC
from data_mcp_intel_deps import IntelDeps

# Labels that make a wallet known-malicious.
MALICIOUS_LABELS = frozenset({"scammer", "mixer", "phisher"})
SOLANA_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_COUNT_HIGH = 500


async def risk_signals(address: str, balances: dict, tags: list, deps: IntelDeps) -> list:
    """Flag names, in the order the profile has always emitted them."""
    out: list = []
    if balances.get("solana", {}).get("sol", 0) > 0:
        r = await deps.rpc_call(
            SOL_RPC,
            "getTokenAccountsByOwner",
            [
                address,
                {"programId": SOLANA_TOKEN_PROGRAM},
                {"encoding": "jsonParsed"},
            ],
            ttl=10,
        )
        if r.get("ok"):
            token_count = len(r["result"].get("value", []))
            if token_count > TOKEN_COUNT_HIGH:
                out.append("high_token_count")
            if token_count < 1:
                out.append("no_tokens")
    if tags and any(t.get("label") in MALICIOUS_LABELS for t in tags):
        out.append("known_malicious")
    return out

#!/usr/bin/env python3
"""
RugMunch Data MCP — FastMCP server instance.

Single shared FastMCP instance; tool modules register on import.
"""

from fastmcp import FastMCP

mcp = FastMCP(
    "RugMunch Data",
    instructions="""
Fleet-wide blockchain intelligence MCP server.
Solana: 4-endpoint RPC fallback (Helius→Labs→Lava→ZAN), caching, rate limiting.
EVM: 3-endpoint RPC per chain (DRPC→PublicNode→LlamaRPC).
Cross-chain: unified wallet profiling, entity resolution, risk scoring.
Private. Tailscale SSH access only.
""",
)

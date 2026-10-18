#!/usr/bin/env python3
"""
RMI x402 MCP Server v5.0 - COMPLETE SYSTEM
==========================================

Complete x402 micropayment gateway with:
- Free/Trial/Premium tier system
- Multi-chain support (Base + Solana)
- AI Guard middleware integration
- 15+ tool bundles with intelligence suite
- Solana & Ethereum MCP server integration

Thin entry point. Implementation lives in cohesive modules under app/mcp/:
- ai_guard.py        — AIGuardWrapper (security middleware)
- models.py          — X402Bundle data model
- facilitator.py     — MultiChainFacilitator config
- bundles.py         — free/trial/standard bundle catalog
- bundles_premium.py — premium bundle catalog
- tool_manager.py    — X402ToolManager (catalog manager)
- server_app.py      — X402MCPServer (FastAPI app + middleware)
"""

import logging

import uvicorn

from app.mcp.ai_guard import AIGuardWrapper
from app.mcp.bundles import load_core_bundles
from app.mcp.bundles_premium import load_premium_bundles
from app.mcp.facilitator import MultiChainFacilitator
from app.mcp.models import X402Bundle
from app.mcp.server_app import X402MCPServer
from app.mcp.tool_manager import X402ToolManager

logger = logging.getLogger("x402_mcp_server")
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

# Create global instance
mcp = X402MCPServer()

__all__ = [
    "AIGuardWrapper",
    "MultiChainFacilitator",
    "X402Bundle",
    "X402MCPServer",
    "X402ToolManager",
    "load_core_bundles",
    "load_premium_bundles",
    "mcp",
]

if __name__ == "__main__":
    # Stateless: no session id to negotiate. Every caller (agents, health
    # probes, curl, the Cinnabox wrappers) can POST a tool call directly.
    app = mcp.streamable_http_app(stateless_http=True)
    uvicorn.run(app, host="0.0.0.0", port=8001)

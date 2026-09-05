#!/usr/bin/env python3
"""
RugMunch Data MCP Server — Fleet-wide blockchain intelligence.
Solana + EVM + Cross-chain entity resolution + DuckDB analytics.
Deployed on Talos, accessible via Tailscale SSH only.

Thin re-export facade. Implementation lives in focused modules:
  data_mcp_config  — env loading, RPC endpoint tables, Postgres/HF settings
  data_mcp_server  — shared FastMCP instance
  data_mcp_infra   — rate limiter, cache, RPC engine, DuckDB/Postgres helpers
  data_mcp_solana  — sol_* tools
  data_mcp_evm     — evm_* + xchain_* tools
  data_mcp_intel   — intel_* tools (Phase 3)
  data_mcp_web     — web_scrape (Pry engine bridge)
"""

import httpx  # noqa: F401  # re-exported: tests monkeypatch data_mcp.httpx.AsyncClient

import data_mcp_evm as _mod_evm  # noqa: F401
import data_mcp_intel as _mod_intel  # noqa: F401
import data_mcp_solana as _mod_solana  # noqa: F401
import data_mcp_web as _mod_web  # noqa: F401
from data_mcp_config import (  # noqa: F401
    EVM_CHAINS,
    EVM_RPC,
    HELIUS_KEY,
    HELIUS_URL,
    HF_BASE_URL,
    HF_HUB_TOKEN,
    HF_REPO,
    PG_DB,
    PG_HOST,
    PG_PASS,
    PG_PORT,
    PG_USER,
    PRY_API_URL,
    SOL_RPC,
    SOLANA_RPC,
)
from data_mcp_evm import (  # noqa: F401
    evm_blockNumber,
    evm_call,
    evm_getBalance,
    evm_getBlock,
    evm_getLogs,
    evm_getTransaction,
    evm_getTransactionReceipt,
    evm_health,
    xchain_health,
    xchain_profile,
)
from data_mcp_infra import (  # noqa: F401
    RL,
    Cache,
    _cache,
    _get_duckdb,
    _get_pg,
    _healthy,
    _rl,
    _rpc_call,
)
from data_mcp_intel import (  # noqa: F401
    intel_archive_schema,
    intel_duckdb_query,
    intel_entity_search,
    intel_wallet_profile,
)
from data_mcp_server import mcp  # noqa: F401
from data_mcp_solana import (  # noqa: F401
    sol_getAccountInfo,
    sol_getBalance,
    sol_getBlock,
    sol_getEpochInfo,
    sol_getMultipleAccounts,
    sol_getProgramAccounts,
    sol_getRecentBlockhash,
    sol_getSignaturesForAddress,
    sol_getSlot,
    sol_getTokenAccounts,
    sol_getTokenLargestAccounts,
    sol_getTokenPrice,
    sol_getTokenSupply,
    sol_health,
    sol_simulateTransaction,
)
from data_mcp_web import web_scrape  # noqa: F401

# Tool modules register on the shared FastMCP instance at import time;
# importing them above is what populates `mcp` with all tools.
_imported_tool_modules = (_mod_evm, _mod_intel, _mod_solana, _mod_web)  # noqa: F841


# ── Main ──────────────────────────────────────────────────────────
def main():
    mcp.run()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
RugMunch Data MCP — intelligence tools facade.

Thin re-export layer kept for import stability. Each intelligence tool owns a
module, and the wallet profile's four sources are separate modules:

  data_mcp_intel_deps      — IntelDeps injection seam (rpc / pg / duckdb)
  data_mcp_intel_balances  — live Solana + EVM balances
  data_mcp_intel_labels    — Postgres entity labels
  data_mcp_intel_history   — DuckDB archive history
  data_mcp_intel_risk      — risk flags
  data_mcp_intel_wallet    — intel_wallet_profile (composes the four)
  data_mcp_intel_entity    — intel_entity_search
  data_mcp_intel_duck      — intel_duckdb_query
  data_mcp_intel_schema    — intel_archive_schema
"""

from data_mcp_intel_duck import intel_duckdb_query  # noqa: F401
from data_mcp_intel_entity import intel_entity_search  # noqa: F401
from data_mcp_intel_schema import intel_archive_schema  # noqa: F401
from data_mcp_intel_wallet import intel_wallet_profile  # noqa: F401

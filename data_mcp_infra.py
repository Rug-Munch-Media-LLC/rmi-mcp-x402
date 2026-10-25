#!/usr/bin/env python3
"""
RugMunch Data MCP — infrastructure facade.

Thin re-export layer kept for import stability. Each responsibility now owns
one module:

  data_mcp_rl     — per-endpoint rate limiter (`RL`, `_rl`)
  data_mcp_cache  — bounded TTL cache (`Cache`, `_cache`)
  data_mcp_rpc    — multi-endpoint RPC engine (`_healthy`, `_rpc_call`)
  data_mcp_duck   — lazy DuckDB handle over HF Hub (`_get_duckdb`)
  data_mcp_pg     — lazy asyncpg connection (`_get_pg`)

New code imports from the owning module; this facade exists so existing
callers (data_mcp.py and the tool modules) keep working unchanged.
"""

from data_mcp_cache import Cache, _cache  # noqa: F401
from data_mcp_duck import _get_duckdb  # noqa: F401
from data_mcp_pg import _get_pg  # noqa: F401
from data_mcp_rl import RL, _rl  # noqa: F401
from data_mcp_rpc import READONLY, _healthy, _rpc_call  # noqa: F401

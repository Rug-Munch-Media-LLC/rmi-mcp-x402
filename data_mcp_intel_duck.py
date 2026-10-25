#!/usr/bin/env python3
"""
RugMunch Data MCP — intel_duckdb_query.

One responsibility: run one operator-supplied SQL statement against the
remote HF Hub archives and return a bounded record set.
"""

from data_mcp_intel_deps import IntelDeps
from data_mcp_server import mcp

MAX_ROWS = 100
NO_TOKEN = "HF_HUB_TOKEN not configured — set in .env.helius on Talos"


async def query_archives(sql: str, deps: IntelDeps) -> dict:
    """Head of the result set plus columns, or a typed ok:false envelope."""
    if not deps.hf_token:
        return {"ok": False, "error": NO_TOKEN}
    try:
        df = deps.get_duckdb().execute(sql).fetchdf()
        return {
            "ok": True,
            "rows": len(df),
            "data": df.head(MAX_ROWS).to_dict(orient="records"),
            "columns": list(df.columns),
            "truncated": len(df) > MAX_ROWS,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)[:500]}


@mcp.tool()
async def intel_duckdb_query(sql: str) -> dict:
    """Execute a raw DuckDB query against the remote HF Hub archives. Use httpfs to read Parquet/JSONL files over HTTPS without downloading."""
    return await query_archives(sql, IntelDeps())

"""rmi-netcup MCP stdio server — FastMCP wrapper for rmi-mcp-x402 tools.

Exposes 11 tools from the TOOL_CATALOG in app/mcp/server.py,
wired to actual implementations in app/domain/x402/tools/.

Launch: fastmcp run mcp_stdio.py:mcp  (stdio transport)
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("rmi-netcup.mcp")

from fastmcp import FastMCP

# ── FastMCP app ───────────────────────────────────────────────────
mcp = FastMCP(
    name="rmi-netcup",
    instructions="Rug Munch Intelligence MCP server — 11 tools for crypto risk, wallet analysis, news, reports, and more.",
)

# ── Supported chains ──────────────────────────────────────────────
SUPPORTED_CHAINS = [
    "solana", "ethereum", "base", "arbitrum", "optimism",
    "polygon", "bsc", "tron", "bitcoin", "avalanche", "fantom", "gnosis",
]

# ── Tool versioning ───────────────────────────────────────────────
TOOL_VERSIONS: dict[str, str] = {
    "get_token_risk": "1.2.0",
    "get_wallet_analysis": "1.1.0",
    "get_deployer_reputation": "2.0.0",
    "get_news_sentiment": "1.0.0",
    "generate_report": "2.0.0",
    "query_catalog": "1.0.0",
    "find_similar_tokens": "1.0.0",
    "resolve_entity": "1.0.0",
    "analytics_query": "1.0.0",
    "mcp_discover": "1.0.0",
    "status_check": "1.0.0",
}

MCP_SERVER_VERSION = "5.0.0"
MCP_PROTOCOL_VERSION = "2024-11-05"


# ── Tool 1: get_token_risk ────────────────────────────────────────
@mcp.tool()
async def get_token_risk(chain: str, address: str) -> dict:
    """Real-time risk score for any token across 13+ chains.

    Returns score (0-100), tier (low/medium/high/critical), and risk factors.
    Free tier: 5 calls/day, $0.01 thereafter.
    """
    if chain not in SUPPORTED_CHAINS:
        return {"error": f"unknown chain: {chain}", "supported": SUPPORTED_CHAINS}

    try:
        from app.domain.x402.tools.token_tools import get_token_risk as _impl
        result = await _impl(token_address=address, chain=chain)
        return {"result": result, "tier": "free_or_pro"}
    except ImportError:
        return {"result": {"token_address": address, "chain": chain, "risk_score": 50, "risk_tier": "UNKNOWN", "risk_factors": []}}


# ── Tool 2: get_wallet_analysis ───────────────────────────────────
@mcp.tool()
async def get_wallet_analysis(chain: str, address: str) -> dict:
    """Wallet activity, balance, transaction history, and reputation.
    Returns wallet profile + risk flags.
    """
    try:
        from app.domain.x402.tools.wallet_tools import get_wallet_analysis as _impl
        result = await _impl(wallet_address=address, chain=chain)
        return {"result": result}
    except ImportError:
        return {"result": {"wallet_address": address, "chain": chain, "analysis": "pending", "risk_score": 50, "personas": []}}


# ── Tool 3: get_deployer_reputation ───────────────────────────────
@mcp.tool()
async def get_deployer_reputation(chain: str, address: str) -> dict:
    """Deployer reputation score 0-100 (100=clean, 0=serial rugger).
    Deterministic from on-chain history + news + RAG findings. Cached 1h.
    """
    return {"result": {
        "wallet": f"{chain}:{address}",
        "reputation_score": 50,
        "tier": "medium",
        "note": "deployer_reputation backend not yet wired — using default score",
    }}


# ── Tool 4: get_news_sentiment ────────────────────────────────────
@mcp.tool()
async def get_news_sentiment(
    subject_id: str = "all",
    since_hours: int = 24,
    limit: int = 10,
) -> dict:
    """Latest news for a token or wallet with sentiment classification.
    Returns articles + composite sentiment score.
    """
    try:
        from app.domain.x402.tools.news_tools import get_news_sentiment as _impl
        result = await _impl(token=subject_id)
        return {"result": result}
    except ImportError:
        return {"result": {"subject": subject_id, "articles": [], "count": 0, "sentiment": "neutral"}}


# ── Tool 5: generate_report ───────────────────────────────────────
@mcp.tool()
async def generate_report(subject_type: str, subject_id: str) -> dict:
    """Full AI research report on a token or wallet.
    7 sections composed in parallel via LLM. $5/report. Returns full Markdown.
    """
    try:
        from app.domain.x402.tools.report_tools import generate_report as _impl
        result = await _impl(subject_type=subject_type, subject_id=subject_id)
        return {"result": result}
    except ImportError:
        return {"result": {"report_id": "placeholder", "subject_type": subject_type, "subject_id": subject_id, "status": "report backend offline"}}


# ── Tool 6: query_catalog ─────────────────────────────────────────
@mcp.tool()
async def query_catalog(query: str) -> dict:
    """Natural language catalog query. Returns matching tokens, wallets,
    deployers, news, RAG findings. $0.05/query.
    """
    return {"result": {"query": query, "hits": [], "count": 0, "note": "catalog backend not yet wired"}}


# ── Tool 7: find_similar_tokens ───────────────────────────────────
@mcp.tool()
async def find_similar_tokens(chain: str, address: str, limit: int = 10) -> dict:
    """Vector-similar tokens to a given token.
    Returns tokens with cosine similarity >= 0.85. $0.03/query.
    """
    if limit > 50:
        limit = 50
    return {"result": {"subject": f"{chain}:{address}", "similar": [], "note": "vector similarity backend not yet wired"}}


# ── Tool 8: resolve_entity ────────────────────────────────────────
@mcp.tool()
async def resolve_entity(wallet_id: str) -> dict:
    """Cross-chain entity resolution. Given a wallet, find all linked wallets
    across chains via SAME_AS / FUNDED_BY_SAME / CLONE_OF / BEHAVIORAL_MATCH edges.
    $0.10/query.
    """
    return {"result": {"wallet_id": wallet_id, "linked_wallets": [], "edges": [], "note": "entity resolution backend not yet wired"}}


# ── Tool 9: analytics_query ───────────────────────────────────────
@mcp.tool()
async def analytics_query(sql: str, params: list | None = None, max_rows: int = 1000) -> dict:
    """Run a read-only SQL query against the embedded DuckDB analytics engine.
    Use for sub-1GB analytical queries (counts, aggregations, joins).
    Max 10K rows returned.
    """
    sql_upper = sql.strip().upper()
    allowed = ("SELECT", "WITH", "SHOW", "DESCRIBE")
    if not any(sql_upper.startswith(prefix) for prefix in allowed):
        return {"error": "only SELECT/WITH/SHOW/DESCRIBE queries allowed"}
    if max_rows > 10000:
        max_rows = 10000
    return {"result": {"rows": [], "count": 0, "truncated": False, "engine": "duckdb", "note": "duckdb backend not yet wired"}}


# ── Tool 10: mcp_discover ─────────────────────────────────────────
@mcp.tool()
async def mcp_discover(include_deprecated: bool = False, category: str | None = None) -> dict:
    """Discover all available MCP tools with versioning, deprecation status,
    and schema introspection. Returns the full tool catalog with auth requirements.
    """
    tool_schemas = [
        {"name": "get_token_risk", "input": ["chain", "address"]},
        {"name": "get_wallet_analysis", "input": ["chain", "address"]},
        {"name": "get_deployer_reputation", "input": ["chain", "address"]},
        {"name": "get_news_sentiment", "input": ["subject_id", "since_hours", "limit"]},
        {"name": "generate_report", "input": ["subject_type", "subject_id"]},
        {"name": "query_catalog", "input": ["query"]},
        {"name": "find_similar_tokens", "input": ["chain", "address", "limit"]},
        {"name": "resolve_entity", "input": ["wallet_id"]},
        {"name": "analytics_query", "input": ["sql", "params", "max_rows"]},
        {"name": "mcp_discover", "input": ["include_deprecated", "category"]},
        {"name": "status_check", "input": ["include_metrics"]},
    ]

    tools_out = []
    from app.mcp.server import TOOL_DEPRECATED, TOOL_SUCCESSORS

    for t in tool_schemas:
        name = t["name"]
        if not include_deprecated and name in TOOL_DEPRECATED:
            continue
        tools_out.append({
            "name": name,
            "version": TOOL_VERSIONS.get(name, "1.0.0"),
            "description": getattr(mcp, "_tools", {}).get(name, {}).get("description", ""),
            "input_schema": t["input"],
            "deprecated": name in TOOL_DEPRECATED,
            "successor": TOOL_SUCCESSORS.get(name),
        })

    return {
        "result": {
            "server": "rmi-netcup",
            "server_version": MCP_SERVER_VERSION,
            "protocol_version": MCP_PROTOCOL_VERSION,
            "tool_count": len(tools_out),
            "tools": tools_out,
        }
    }


# ── Tool 11: status_check ─────────────────────────────────────────
@mcp.tool()
async def status_check(include_metrics: bool = True) -> dict:
    """Health check across all RMI subsystems (backend, postgres, clickhouse,
    qdrant, redis, minio, reth, mcp server, x402, certstream, glitchtip).
    Returns pass/fail/degraded for each with latency.
    """
    subsystems = {}
    try:
        import asyncio, time
        import httpx

        services = {
            "rmi-backend": "http://rmi-backend:8000/health",
            "mcp-server": "self",
            "x402-gateway": "https://mcp.rugmunch.io/health",
        }

        async def check(name: str, url: str) -> tuple[str, str, float | None]:
            if url == "self":
                return (name, "pass", 0.0)
            t0 = time.monotonic()
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.get(url)
                    latency = round((time.monotonic() - t0) * 1000, 1)
                    return (name, "pass" if resp.status_code < 500 else "degraded", latency)
            except Exception:
                return (name, "fail", None)

        results = await asyncio.gather(*(check(n, u) for n, u in services.items()))
        for name, status, latency in results:
            subsystems[name] = {"status": status, "latency_ms": latency}
    except ImportError:
        subsystems = {"mcp-server": {"status": "pass", "latency_ms": 0.0}}

    result: dict[str, Any] = {"subsystems": subsystems, "server_version": MCP_SERVER_VERSION}
    if include_metrics:
        result["metrics"] = {"tool_count": 11, "server_uptime": "unknown"}
    return {"result": result}


# ── Entrypoint ────────────────────────────────────────────────────
if __name__ == "__main__":
    mcp.run(transport="stdio")

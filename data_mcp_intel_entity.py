#!/usr/bin/env python3
"""
RugMunch Data MCP — intel_entity_search.

One responsibility: find wallets by entity name, label tag or entity type in
the local Postgres label database.
"""

from data_mcp_intel_deps import IntelDeps
from data_mcp_server import mcp

SEARCH_QUERY = """
    SELECT wl.wallet_address, wl.label_tag, wl.confidence_score, wl.chain,
           ep.entity_name, ep.entity_type, ep.description
    FROM wallet_labels wl
    JOIN entity_profiles ep ON wl.entity_id = ep.id
    WHERE
        ep.entity_name ILIKE $1
        OR wl.label_tag ILIKE $1
        OR ep.entity_type::text ILIKE $1
    ORDER BY wl.confidence_score DESC
    LIMIT 50
"""
NO_POSTGRES = "Postgres not configured (POSTGRES_PASSWORD not set)"


async def search_entities(query: str, deps: IntelDeps) -> dict:
    """Up to 50 matching labels/entities, or a typed ok:false envelope."""
    conn = await deps.get_pg()
    if not conn:
        return {"ok": False, "error": NO_POSTGRES}
    try:
        rows = await conn.fetch(SEARCH_QUERY, f"%{query}%")
        results = [
            {
                "address": row["wallet_address"],
                "label": row["label_tag"],
                "confidence": float(row["confidence_score"]),
                "chain": row["chain"],
                "entity": row["entity_name"],
                "type": row["entity_type"],
                "description": row["description"],
            }
            for row in rows
        ]
        await conn.close()
        return {"ok": True, "query": query, "matches": len(results), "results": results}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


@mcp.tool()
async def intel_entity_search(query: str) -> dict:
    """Search entity profiles in the local Postgres database. Find wallets by entity name, label tag, or type."""
    return await search_entities(query, IntelDeps())

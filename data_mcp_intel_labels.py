#!/usr/bin/env python3
"""
RugMunch Data MCP — Postgres entity labels for the wallet profile.

One responsibility: look up the wallet's curated labels and entity profile,
best-confidence first.
"""

from data_mcp_intel_deps import IntelDeps

TAG_QUERY = """
    SELECT wl.label_tag, wl.confidence_score, wl.chain, wl.source,
           ep.entity_name, ep.entity_type
    FROM wallet_labels wl
    LEFT JOIN entity_profiles ep ON wl.entity_id = ep.id
    WHERE wl.wallet_address = $1
    ORDER BY wl.confidence_score DESC
    LIMIT 20
"""


async def entity_tags(address: str, deps: IntelDeps) -> list:
    """Up to 20 labels, or [] when Postgres is unconfigured/unreachable."""
    conn = await deps.get_pg()
    if not conn:
        return []
    try:
        rows = await conn.fetch(TAG_QUERY, address)
        tags = [
            {
                "label": row["label_tag"],
                "confidence": float(row["confidence_score"]),
                "chain": row["chain"],
                "source": row["source"],
                "entity": row["entity_name"],
                "entity_type": row["entity_type"],
            }
            for row in rows
        ]
        await conn.close()
        return tags
    except Exception as e:
        return [{"error": str(e)[:200]}]

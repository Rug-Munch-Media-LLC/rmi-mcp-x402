#!/usr/bin/env python3
"""
RugMunch Data MCP — lazy asyncpg connection for the entity-label database.

One responsibility: open one connection to Postgres, or return None when the
deployment has no credentials configured.
"""

import asyncpg

from data_mcp_config import PG_DB, PG_HOST, PG_PASS, PG_PORT, PG_USER


async def _get_pg():
    """Lazy-init asyncpg connection pool."""
    if not PG_PASS:
        return None
    try:
        conn = await asyncpg.connect(
            host=PG_HOST, port=PG_PORT, database=PG_DB, user=PG_USER, password=PG_PASS, timeout=10
        )
        return conn
    except Exception:
        return None

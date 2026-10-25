#!/usr/bin/env python3
"""
RugMunch Data MCP — lazy DuckDB handle with HF Hub credentials.

One responsibility: hand out a DuckDB connection that can read the remote
Hugging Face archives over httpfs.
"""

from data_mcp_config import HF_HUB_TOKEN

_initialized = False

HTTPFS = "INSTALL httpfs; LOAD httpfs;"
HF_SECRET = """
                CREATE SECRET hf_token (
                    TYPE HTTP,
                    EXTRA_HTTP_HEADERS MAP {{
                        'Authorization': 'Bearer {token}'
                    }}
                );
            """


def _init_httpfs(db, token: str = HF_HUB_TOKEN) -> None:
    """Load httpfs and, when a token is configured, register the HF secret."""
    db.execute(HTTPFS)
    if token:
        # DuckDB DDL with an env-sourced token; no user input reaches this.
        # nosemgrep: sqlalchemy-execute-raw-query
        db.execute(HF_SECRET.format(token=token))


def _get_duckdb():
    """Lazy-init DuckDB with httpfs for HF Hub remote queries."""
    global _initialized
    import duckdb

    db = duckdb.connect()
    if not _initialized:
        _init_httpfs(db)
        _initialized = True
    return db

#!/usr/bin/env python3
"""
RugMunch Data MCP — DuckDB archive history for the wallet profile.

One responsibility: summarise the wallet's archived transactions from the
Hugging Face Hub JSONL archives, or say why nothing came back.
"""

from data_mcp_config import HF_BASE_URL
from data_mcp_intel_balances import SOLANA_MAX_LEN
from data_mcp_intel_deps import IntelDeps

NO_ARCHIVES = "Archives may not be populated yet — run the HF Space indexer first"


async def archive_history(address: str, deps: IntelDeps) -> dict:
    """{total_matches, first_seen, last_seen, unique_counterparties, sample_tx} or {}."""
    if not deps.hf_token:
        return {}
    try:
        db = deps.get_duckdb()
        chain_dir = "solana" if len(address) <= SOLANA_MAX_LEN else "evm"
        archives_url = f"{HF_BASE_URL}/{chain_dir}/"
        # Parameterized: address is user-supplied (MCP tool input) and must
        # never be interpolated into SQL.
        query = f"""
            SELECT *
            FROM read_json_auto('{archives_url}**/*.jsonl.gz', union_by_name=true)
            WHERE
                (LOWER(from_address) = LOWER(?) OR LOWER(to_address) = LOWER(?))
            LIMIT 100
        """
        try:
            # f-string builds only the env-sourced URL; address is parameterized.
            # nosemgrep: sqlalchemy-execute-raw-query
            df = db.execute(query, [address, address]).fetchdf()
            if len(df) == 0:
                return {}
            return {
                "total_matches": len(df),
                "first_seen": str(df["block_time"].min()) if "block_time" in df.columns else None,
                "last_seen": str(df["block_time"].max()) if "block_time" in df.columns else None,
                "unique_counterparties": int(df["from_address"].nunique())
                if "from_address" in df.columns
                else 0,
                "sample_tx": df.head(5).to_dict(orient="records"),
            }
        except Exception as e:
            return {"error": str(e)[:200], "note": NO_ARCHIVES}
    except Exception as e:
        return {"error": str(e)[:200]}

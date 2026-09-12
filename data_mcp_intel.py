#!/usr/bin/env python3
"""
RugMunch Data MCP — intelligence tools (Phase 3).

Cross-chain wallet intelligence combining live RPC balances, Postgres
entity tags, and DuckDB httpfs queries against HF Hub archives.
"""

import time

from data_mcp_config import EVM_RPC, HF_BASE_URL, HF_HUB_TOKEN, HF_REPO, SOL_RPC
from data_mcp_infra import _get_duckdb, _get_pg, _rpc_call
from data_mcp_server import mcp

# ═══════════════════════════════════════════════════════════════════
# PHASE 3: INTELLIGENCE TOOLS
# ═══════════════════════════════════════════════════════════════════


@mcp.tool()
async def intel_wallet_profile(address: str) -> dict:
    """
    Comprehensive cross-chain wallet intelligence profile.
    Queries live RPCs (Solana + all EVM chains) + Postgres entity tags + DuckDB historical archives.
    This is the primary intelligence tool — combines live data with historical patterns and entity labels.
    """
    result = {
        "address": address,
        "queried_at": time.time(),
        "live_balances": {},
        "entity_tags": [],
        "historical_patterns": {},
        "risk_signals": [],
    }

    # 1. Live balance checks (Solana)
    if len(address) <= 44:
        r = await _rpc_call(SOL_RPC, "getBalance", [address], ttl=5)
        if r.get("ok"):
            lamports = r["result"]
            result["live_balances"]["solana"] = {
                "sol": lamports / 1e9,
                "lamports": lamports,
                "endpoint": r["endpoint"],
            }

    # 2. Live balance checks (EVM chains)
    for chain in ["ethereum", "base", "arbitrum"]:
        if address.startswith("0x") or len(address) == 42:
            eps = EVM_RPC.get(chain, [])
            r = await _rpc_call(eps, "eth_getBalance", [address, "latest"], ttl=5)
            if r.get("ok") and r["result"] != "0x0":
                bal_eth = int(r["result"], 16) / 1e18
                result["live_balances"][chain] = {
                    "eth": round(bal_eth, 6),
                    "wei": int(r["result"], 16),
                    "endpoint": r["endpoint"],
                }

    # 3. Postgres entity tag lookup
    conn = await _get_pg()
    if conn:
        try:
            rows = await conn.fetch(
                """
                SELECT wl.label_tag, wl.confidence_score, wl.chain, wl.source,
                       ep.entity_name, ep.entity_type
                FROM wallet_labels wl
                LEFT JOIN entity_profiles ep ON wl.entity_id = ep.id
                WHERE wl.wallet_address = $1
                ORDER BY wl.confidence_score DESC
                LIMIT 20
            """,
                address,
            )
            for row in rows:
                result["entity_tags"].append(
                    {
                        "label": row["label_tag"],
                        "confidence": float(row["confidence_score"]),
                        "chain": row["chain"],
                        "source": row["source"],
                        "entity": row["entity_name"],
                        "entity_type": row["entity_type"],
                    }
                )
            await conn.close()
        except Exception as e:
            result["entity_tags"] = [{"error": str(e)[:200]}]

    # 4. DuckDB historical archive query (if HF Hub configured)
    if HF_HUB_TOKEN:
        try:
            db = _get_duckdb()
            chain_dir = "solana" if len(address) <= 44 else "evm"
            archives_url = f"{HF_BASE_URL}/{chain_dir}/"

            # Parameterized: address is user-supplied (MCP tool input) and
            # must never be interpolated into SQL.
            query = f"""
                SELECT *
                FROM read_json_auto('{archives_url}**/*.jsonl.gz', union_by_name=true)
                WHERE
                    (LOWER(from_address) = LOWER(?) OR LOWER(to_address) = LOWER(?))
                LIMIT 100
            """
            try:
                df = db.execute(query, [address, address]).fetchdf()
                if len(df) > 0:
                    result["historical_patterns"] = {
                        "total_matches": len(df),
                        "first_seen": str(df["block_time"].min())
                        if "block_time" in df.columns
                        else None,
                        "last_seen": str(df["block_time"].max())
                        if "block_time" in df.columns
                        else None,
                        "unique_counterparties": int(df["from_address"].nunique())
                        if "from_address" in df.columns
                        else 0,
                        "sample_tx": df.head(5).to_dict(orient="records") if len(df) > 0 else [],
                    }
            except Exception as e:
                result["historical_patterns"] = {
                    "error": str(e)[:200],
                    "note": "Archives may not be populated yet — run the HF Space indexer first",
                }
        except Exception as e:
            result["historical_patterns"] = {"error": str(e)[:200]}

    # 5. Risk signals
    if result["live_balances"].get("solana", {}).get("sol", 0) > 0:
        # Check SPL token holdings for scam tokens
        r = await _rpc_call(
            SOL_RPC,
            "getTokenAccountsByOwner",
            [
                address,
                {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
                {"encoding": "jsonParsed"},
            ],
            ttl=10,
        )
        if r.get("ok"):
            token_count = len(r["result"].get("value", []))
            if token_count > 500:
                result["risk_signals"].append("high_token_count")
            if token_count < 1:
                result["risk_signals"].append("no_tokens")

    if result["entity_tags"]:
        scam_tags = [
            t for t in result["entity_tags"] if t["label"] in ["scammer", "mixer", "phisher"]
        ]
        if scam_tags:
            result["risk_signals"].append("known_malicious")

    return result


@mcp.tool()
async def intel_entity_search(query: str) -> dict:
    """Search entity profiles in the local Postgres database. Find wallets by entity name, label tag, or type."""
    conn = await _get_pg()
    if not conn:
        return {"ok": False, "error": "Postgres not configured (POSTGRES_PASSWORD not set)"}

    try:
        rows = await conn.fetch(
            """
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
        """,
            f"%{query}%",
        )

        results = []
        for row in rows:
            results.append(
                {
                    "address": row["wallet_address"],
                    "label": row["label_tag"],
                    "confidence": float(row["confidence_score"]),
                    "chain": row["chain"],
                    "entity": row["entity_name"],
                    "type": row["entity_type"],
                    "description": row["description"],
                }
            )

        await conn.close()
        return {"ok": True, "query": query, "matches": len(results), "results": results}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


@mcp.tool()
async def intel_duckdb_query(sql: str) -> dict:
    """Execute a raw DuckDB query against the remote HF Hub archives. Use httpfs to read Parquet/JSONL files over HTTPS without downloading."""
    if not HF_HUB_TOKEN:
        return {"ok": False, "error": "HF_HUB_TOKEN not configured — set in .env.helius on Talos"}

    try:
        db = _get_duckdb()
        result = db.execute(sql).fetchdf()
        return {
            "ok": True,
            "rows": len(result),
            "data": result.head(100).to_dict(orient="records"),
            "columns": list(result.columns),
            "truncated": len(result) > 100,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)[:500]}


@mcp.tool()
async def intel_archive_schema() -> dict:
    """Discover the schema and available data in the Hugging Face Hub archives. Lists all chains, networks, and file counts."""
    return {
        "ok": True,
        "storage": {
            "provider": "Hugging Face Hub",
            "repo": HF_REPO,
            "format": "gzip-compressed JSONL + Parquet",
        },
        "chains": {
            "solana": {
                "networks": ["solana-mainnet"],
                "fields": [
                    "block_number",
                    "block_time",
                    "tx_hash",
                    "from_address",
                    "to_address",
                    "token_mint",
                    "amount",
                    "program_id",
                    "instruction_type",
                    "slot",
                ],
                "note": "Indexed via SQD Network Data Lake. Free, no rate limits.",
            },
            "evm": {
                "networks": ["ethereum-mainnet", "base-mainnet", "arbitrum-mainnet"],
                "fields": [
                    "block_number",
                    "block_time",
                    "tx_hash",
                    "from_address",
                    "to_address",
                    "value_wei",
                    "gas_price_wei",
                    "gas_used",
                    "method_id",
                    "chain",
                ],
                "note": "Indexed via DRPC + PublicNode + LlamaRPC fallback chain.",
            },
        },
        "query_examples": {
            "wallet_history": "SELECT * FROM read_json_auto('https://huggingface.co/datasets/rugmunchmedia/solana-archives/resolve/main/solana/solana-mainnet/*.jsonl.gz', union_by_name=true) WHERE from_address = 'YOUR_ADDRESS' LIMIT 100",
            "token_activity": "SELECT COUNT(*) AS tx_count, SUM(CAST(amount AS DOUBLE)) AS volume FROM read_json_auto('...') WHERE token_mint = 'YOUR_MINT' AND block_time > '2026-01-01'",
            "cross_chain": "SELECT 'solana' AS chain, COUNT(*) AS count FROM read_json_auto('...solana...') UNION ALL SELECT 'ethereum', COUNT(*) FROM read_json_auto('...evm/ethereum...')",
        },
    }

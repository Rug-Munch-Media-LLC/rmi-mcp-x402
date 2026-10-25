#!/usr/bin/env python3
"""
RugMunch Data MCP — intel_archive_schema.

One responsibility: publish the static description of the HF Hub archives —
chains, fields and query examples — without touching the network.
"""

from data_mcp_config import HF_REPO
from data_mcp_server import mcp

ARCHIVE_SCHEMA = {
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


@mcp.tool()
async def intel_archive_schema() -> dict:
    """Discover the schema and available data in the Hugging Face Hub archives. Lists all chains, networks, and file counts."""
    return dict(ARCHIVE_SCHEMA)

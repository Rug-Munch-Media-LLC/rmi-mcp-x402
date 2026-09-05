#!/usr/bin/env python3
"""
RugMunch Data MCP — Solana tools.

Health checks, account/transaction queries, token data, Jupiter pricing.
"""

import time

import httpx

from data_mcp_config import SOL_RPC
from data_mcp_infra import _rpc_call
from data_mcp_server import mcp

# ═══════════════════════════════════════════════════════════════════
# SOLANA TOOLS
# ═══════════════════════════════════════════════════════════════════


@mcp.tool()
async def sol_health() -> dict:
    """Health check — all Solana RPC endpoints, active endpoint, latencies."""
    results = {}
    async with httpx.AsyncClient(timeout=5.0) as cl:
        for ep in SOL_RPC:
            try:
                t0 = time.monotonic()
                await cl.post(ep["url"], json={"jsonrpc": "2.0", "id": 1, "method": "getHealth"})
                ms = (time.monotonic() - t0) * 1000
                results[ep["name"]] = {"status": "ok", "latency_ms": round(ms)}
            except Exception as e:
                results[ep["name"]] = {"status": "error", "error": str(e)[:100]}
    active = next((n for n, r in results.items() if r["status"] == "ok"), None)
    return {"chain": "solana", "endpoints": results, "active": active}


@mcp.tool()
async def sol_getTransaction(signature: str) -> dict:
    """Full Solana transaction details by signature (parsed instructions, accounts, logs)."""
    return await _rpc_call(
        SOL_RPC,
        "getTransaction",
        [signature, {"maxSupportedTransactionVersion": 0, "encoding": "jsonParsed"}],
        ttl=60,
    )


@mcp.tool()
async def sol_getAccountInfo(address: str) -> dict:
    """Solana account info: lamports, owner, executable, data."""
    return await _rpc_call(SOL_RPC, "getAccountInfo", [address, {"encoding": "jsonParsed"}], ttl=15)


@mcp.tool()
async def sol_getBalance(address: str) -> dict:
    """SOL balance in lamports for an address."""
    return await _rpc_call(SOL_RPC, "getBalance", [address], ttl=10)


@mcp.tool()
async def sol_getTokenAccounts(address: str) -> dict:
    """All SPL token accounts owned by an address (mint, amount, decimals)."""
    return await _rpc_call(
        SOL_RPC,
        "getTokenAccountsByOwner",
        [
            address,
            {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
            {"encoding": "jsonParsed"},
        ],
        ttl=15,
    )


@mcp.tool()
async def sol_getBlock(slot: int) -> dict:
    """Solana block details by slot (transactions, blockhash, time)."""
    return await _rpc_call(
        SOL_RPC,
        "getBlock",
        [
            slot,
            {
                "maxSupportedTransactionVersion": 0,
                "encoding": "jsonParsed",
                "transactionDetails": "full",
                "rewards": False,
            },
        ],
        ttl=120,
    )


@mcp.tool()
async def sol_getSignaturesForAddress(address: str, limit: int = 20) -> dict:
    """Recent transaction signatures for a Solana address."""
    return await _rpc_call(
        SOL_RPC, "getSignaturesForAddress", [address, {"limit": min(limit, 100)}], ttl=10
    )


@mcp.tool()
async def sol_getMultipleAccounts(addresses: list[str]) -> dict:
    """Batch account info for up to 100 Solana addresses."""
    return await _rpc_call(
        SOL_RPC, "getMultipleAccounts", [addresses[:100], {"encoding": "jsonParsed"}], ttl=15
    )


@mcp.tool()
async def sol_getTokenSupply(mint: str) -> dict:
    """Total supply of an SPL token by mint address."""
    return await _rpc_call(SOL_RPC, "getTokenSupply", [mint], ttl=30)


@mcp.tool()
async def sol_getTokenLargestAccounts(mint: str) -> dict:
    """Top 20 token holders for a mint address."""
    return await _rpc_call(SOL_RPC, "getTokenLargestAccounts", [mint], ttl=30)


@mcp.tool()
async def sol_getSlot() -> dict:
    """Current Solana slot number."""
    return await _rpc_call(SOL_RPC, "getSlot", [], ttl=0)


@mcp.tool()
async def sol_getEpochInfo() -> dict:
    """Current Solana epoch info: epoch, slot index, absolute slot, block height."""
    return await _rpc_call(SOL_RPC, "getEpochInfo", [], ttl=30)


@mcp.tool()
async def sol_getProgramAccounts(program_id: str) -> dict:
    """All accounts owned by a Solana program."""
    return await _rpc_call(
        SOL_RPC,
        "getProgramAccounts",
        [program_id, {"encoding": "jsonParsed", "filters": []}],
        ttl=30,
    )


@mcp.tool()
async def sol_getTokenPrice(mint: str) -> dict:
    """Token price from Jupiter API (USD)."""
    try:
        async with httpx.AsyncClient(timeout=8.0) as cl:
            r = await cl.get(f"https://api.jup.ag/price/v2?ids={mint}")
            if r.status_code == 200:
                d = r.json()
                t = d.get("data", {}).get(mint, {})
                return {
                    "ok": True,
                    "mint": mint,
                    "price_usd": t.get("price"),
                    "endpoint": "jupiter",
                }
    except Exception:
        pass  # noqa: E722
    return {"ok": False, "error": "Jupiter fetch failed", "mint": mint}


@mcp.tool()
async def sol_simulateTransaction(tx_base64: str) -> dict:
    """Simulate Solana transaction without submitting."""
    return await _rpc_call(
        SOL_RPC,
        "simulateTransaction",
        [tx_base64, {"encoding": "base64", "commitment": "processed"}],
        ttl=0,
    )


@mcp.tool()
async def sol_getRecentBlockhash() -> dict:
    """Recent Solana blockhash for transaction building."""
    return await _rpc_call(SOL_RPC, "getLatestBlockhash", [{"commitment": "finalized"}], ttl=0)

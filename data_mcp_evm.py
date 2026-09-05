#!/usr/bin/env python3
"""
RugMunch Data MCP — EVM + cross-chain tools.

Per-chain EVM RPC queries (ethereum, base, arbitrum) and unified
cross-chain health / wallet profiling.
"""

import time

import httpx

from data_mcp_config import EVM_RPC, SOL_RPC
from data_mcp_infra import _rpc_call
from data_mcp_server import mcp

# ═══════════════════════════════════════════════════════════════════
# EVM TOOLS
# ═══════════════════════════════════════════════════════════════════


@mcp.tool()
async def evm_health(chain: str = "ethereum") -> dict:
    """Health check for EVM RPC endpoints. Chain: ethereum, base, or arbitrum."""
    endpoints = EVM_RPC.get(chain, [])
    if not endpoints:
        return {"ok": False, "error": f"Unknown chain: {chain}"}
    results = {}
    async with httpx.AsyncClient(timeout=5.0) as cl:
        for ep in endpoints:
            try:
                t0 = time.monotonic()
                r = await cl.post(
                    ep["url"],
                    json={"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []},
                )
                ms = (time.monotonic() - t0) * 1000
                results[ep["name"]] = {
                    "status": "ok",
                    "latency_ms": round(ms),
                    "block": int(r.json().get("result", "0x0"), 16),
                }
            except Exception as e:
                results[ep["name"]] = {"status": "error", "error": str(e)[:100]}
    active = next((n for n, r in results.items() if r["status"] == "ok"), None)
    return {"chain": chain, "endpoints": results, "active": active}


@mcp.tool()
async def evm_getBlock(chain: str = "ethereum", block: str = "latest") -> dict:
    """Get EVM block by number or 'latest'. Chain: ethereum, base, arbitrum."""
    endpoints = EVM_RPC.get(chain, [])
    if not endpoints:
        return {"ok": False, "error": f"Unknown chain: {chain}"}
    if block != "latest":
        block = hex(int(block))
    return await _rpc_call(endpoints, "eth_getBlockByNumber", [block, True], ttl=30)


@mcp.tool()
async def evm_getTransaction(chain: str, tx_hash: str) -> dict:
    """Get EVM transaction by hash. Chain: ethereum, base, arbitrum."""
    endpoints = EVM_RPC.get(chain, [])
    if not endpoints:
        return {"ok": False, "error": f"Unknown chain: {chain}"}
    return await _rpc_call(endpoints, "eth_getTransactionByHash", [tx_hash], ttl=60)


@mcp.tool()
async def evm_getBalance(chain: str, address: str) -> dict:
    """Get ETH/native token balance for an address in wei."""
    endpoints = EVM_RPC.get(chain, [])
    if not endpoints:
        return {"ok": False, "error": f"Unknown chain: {chain}"}
    return await _rpc_call(endpoints, "eth_getBalance", [address, "latest"], ttl=10)


@mcp.tool()
async def evm_getTransactionReceipt(chain: str, tx_hash: str) -> dict:
    """Get EVM transaction receipt (logs, gas used, status)."""
    endpoints = EVM_RPC.get(chain, [])
    if not endpoints:
        return {"ok": False, "error": f"Unknown chain: {chain}"}
    return await _rpc_call(endpoints, "eth_getTransactionReceipt", [tx_hash], ttl=60)


@mcp.tool()
async def evm_blockNumber(chain: str = "ethereum") -> dict:
    """Get current block number for an EVM chain."""
    endpoints = EVM_RPC.get(chain, [])
    if not endpoints:
        return {"ok": False, "error": f"Unknown chain: {chain}"}
    return await _rpc_call(endpoints, "eth_blockNumber", [], ttl=0)


@mcp.tool()
async def evm_getLogs(
    chain: str, address: str, fromBlock: str = "0x0", toBlock: str = "latest"
) -> dict:
    """Get event logs for an EVM contract address."""
    endpoints = EVM_RPC.get(chain, [])
    if not endpoints:
        return {"ok": False, "error": f"Unknown chain: {chain}"}
    return await _rpc_call(
        endpoints,
        "eth_getLogs",
        [{"address": address, "fromBlock": fromBlock, "toBlock": toBlock}],
        ttl=30,
    )


@mcp.tool()
async def evm_call(chain: str, to: str, data: str) -> dict:
    """Execute a read-only contract call (eth_call). Chain: ethereum, base, arbitrum."""
    endpoints = EVM_RPC.get(chain, [])
    if not endpoints:
        return {"ok": False, "error": f"Unknown chain: {chain}"}
    return await _rpc_call(endpoints, "eth_call", [{"to": to, "data": data}, "latest"], ttl=5)


# ═══════════════════════════════════════════════════════════════════
# CROSS-CHAIN TOOLS
# ═══════════════════════════════════════════════════════════════════


@mcp.tool()
async def xchain_health() -> dict:
    """Health check for ALL chains (Solana + all EVM). Returns per-chain status."""
    results = {}
    async with httpx.AsyncClient(timeout=5.0) as cl:
        # Solana
        for ep in SOL_RPC:
            try:
                r = await cl.post(
                    ep["url"], json={"jsonrpc": "2.0", "id": 1, "method": "getHealth"}
                )
                if r.json().get("result") == "ok":
                    results["solana"] = "ok"
                    break
            except Exception:
                pass  # noqa: E722
        if "solana" not in results:
            results["solana"] = "down"
        # EVM
        for chain, eps in EVM_RPC.items():
            for ep in eps:
                try:
                    r = await cl.post(
                        ep["url"],
                        json={"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []},
                    )
                    b = int(r.json().get("result", "0x0"), 16)
                    results[chain] = f"ok (block {b})"
                    break
                except Exception:
                    pass  # noqa: E722
            if chain not in results:
                results[chain] = "down"
    return {"chains": results, "timestamp": time.time()}


@mcp.tool()
async def xchain_profile(address: str) -> dict:
    """Cross-chain wallet profile. Queries Solana + Ethereum + Base + Arbitrum for the SAME address pattern."""
    # Check Solana (base58 address)
    sol_info = None
    if len(address) <= 44:
        r = await _rpc_call(SOL_RPC, "getBalance", [address], ttl=10)
        sol_info = {"balance_lamports": r.get("result"), "endpoint": r.get("endpoint")}

    # Check EVM (hex address)
    evm_info = {}
    if address.startswith("0x") or len(address) == 42:
        for chain in ["ethereum", "base", "arbitrum"]:
            eps = EVM_RPC.get(chain, [])
            r = await _rpc_call(eps, "eth_getBalance", [address, "latest"], ttl=10)
            bal = r.get("result")
            if bal and bal != "0x0":
                evm_info[chain] = {"balance_wei": int(bal, 16), "endpoint": r.get("endpoint")}

    return {
        "address": address,
        "solana": sol_info or "no activity",
        "evm": evm_info or "no activity",
        "profile_type": "solana" if sol_info else "evm" if evm_info else "unknown",
    }

#!/usr/bin/env python3
"""
RugMunch Data MCP — live on-chain balances for the wallet profile.

One responsibility: read the wallet's native balance on Solana and on every
EVM chain the profile covers, keeping only endpoints that answered.
"""

from data_mcp_config import EVM_RPC, SOL_RPC
from data_mcp_intel_deps import IntelDeps

# Solana addresses are base58 and never longer than this.
SOLANA_MAX_LEN = 44
# Chains the profile always probes; a chain with no answer is simply omitted.
PROFILE_EVM_CHAINS = ("ethereum", "base", "arbitrum")


async def live_balances(address: str, deps: IntelDeps) -> dict:
    """{chain: balance} for Solana and each configured EVM chain."""
    out: dict = {}
    if len(address) <= SOLANA_MAX_LEN:
        r = await deps.rpc_call(SOL_RPC, "getBalance", [address], ttl=5)
        if r.get("ok"):
            lamports = r["result"]
            out["solana"] = {
                "sol": lamports / 1e9,
                "lamports": lamports,
                "endpoint": r["endpoint"],
            }
    if address.startswith("0x") or len(address) == 42:
        for chain in PROFILE_EVM_CHAINS:
            r = await deps.rpc_call(
                EVM_RPC.get(chain, []), "eth_getBalance", [address, "latest"], ttl=5
            )
            if r.get("ok") and r["result"] != "0x0":
                wei = int(r["result"], 16)
                out[chain] = {
                    "eth": round(wei / 1e18, 6),
                    "wei": wei,
                    "endpoint": r["endpoint"],
                }
    return out

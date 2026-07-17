#!/usr/bin/env python3
"""
Private Solana MCP Server — Multi-RPC fallback, caching, rate-limited.
Deployed on Talos, accessible via Tailscale SSH only.
RPC chain: Helius → Solana Labs → Lava → ZAN
"""

import os
import time
import asyncio
import hashlib
import json
from typing import Optional, Any
from collections import OrderedDict
from datetime import datetime, timedelta

import httpx
from fastmcp import FastMCP


# Load Helius API key from local env file (set via fleet deployment)
_env_file = os.path.join(os.path.dirname(__file__), '.env.helius')
if os.path.exists(_env_file):
    with open(_env_file) as _f:
        for _line in _f:
            if '=' in _line:
                _k, _v = _line.strip().split('=', 1)
                os.environ.setdefault(_k, _v)

# ── Configuration ──────────────────────────────────────────────────
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
HELIUS_URL = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}" if HELIUS_API_KEY else None

RPC_ENDPOINTS = [
    {"name": "helius",      "url": HELIUS_URL,                           "rate_limit": 50,  "cooldown": 0},
    {"name": "solana-labs", "url": "https://api.mainnet-beta.solana.com", "rate_limit": 10,  "cooldown": 0},
    {"name": "lava",        "url": "https://solana.lava.build",           "rate_limit": 20,  "cooldown": 0},
    {"name": "zan",         "url": "https://api.zan.top/solana-mainnet",  "rate_limit": 20,  "cooldown": 0},
]

# Remove Helius if no key configured
if not HELIUS_API_KEY:
    RPC_ENDPOINTS = [e for e in RPC_ENDPOINTS if e["name"] != "helius"]

# ── Rate Limiting ───────────────────────────────────────────────────
class RateLimiter:
    def __init__(self):
        self.windows: dict[str, list[float]] = {}
    
    def allow(self, endpoint: str, max_per_sec: float) -> bool:
        now = time.monotonic()
        if endpoint not in self.windows:
            self.windows[endpoint] = []
        window = [t for t in self.windows[endpoint] if now - t < 1.0]
        self.windows[endpoint] = window
        if len(window) >= max_per_sec:
            return False
        window.append(now)
        return True

_rate_limiter = RateLimiter()

# ── Simple Cache ────────────────────────────────────────────────────
class TTLCache:
    def __init__(self, maxsize: int = 500):
        self.store: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self.maxsize = maxsize
    
    def _key(self, method: str, params: Any) -> str:
        raw = f"{method}:{json.dumps(params, sort_keys=True)}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]
    
    def get(self, method: str, params: Any, ttl: int = 30) -> Optional[Any]:
        key = self._key(method, params)
        if key in self.store:
            val, expiry = self.store[key]
            if time.time() < expiry:
                self.store.move_to_end(key)
                return val
            del self.store[key]
        return None
    
    def set(self, method: str, params: Any, value: Any, ttl: int = 30):
        key = self._key(method, params)
        if key in self.store:
            self.store.move_to_end(key)
        else:
            if len(self.store) >= self.maxsize:
                self.store.popitem(last=False)
        self.store[key] = (value, time.time() + ttl)

_cache = TTLCache()

# ── RPC Client with Fallback ────────────────────────────────────────
def _get_healthy_endpoints():
    """Return endpoints ordered by health (not in cooldown)."""
    now = time.monotonic()
    healthy = []
    for ep in RPC_ENDPOINTS:
        if ep["url"] is None:
            continue
        if now < ep["cooldown"]:
            continue
        if _rate_limiter.allow(ep["name"], ep["rate_limit"]):
            healthy.append(ep)
    if not healthy:
        for ep in RPC_ENDPOINTS:
            if ep["url"]:
                ep["cooldown"] = 0
        healthy = [e for e in RPC_ENDPOINTS if e["url"]]
    return healthy

async def rpc_call(method: str, params: list = None, ttl: int = 10) -> dict:
    """Make a Solana RPC call with fallback chain and caching."""
    readonly_methods = {
        "getTransaction", "getAccountInfo", "getBalance", "getTokenAccountsByOwner",
        "getBlock", "getRecentBlockhash", "getSignaturesForAddress",
        "getMultipleAccounts", "getProgramAccounts", "getTokenLargestAccounts",
        "getTokenSupply", "getBlockHeight", "getSlot", "getHealth",
        "getEpochInfo", "getVersion", "getIdentity"
    }
    
    if method in readonly_methods:
        cached = _cache.get(method, params, ttl)
        if cached is not None:
            return {"ok": True, "result": cached, "endpoint": "cache", "cached": True}
    
    endpoints = _get_healthy_endpoints()
    if not endpoints:
        return {"ok": False, "error": "No healthy RPC endpoints available", "endpoint": None}
    
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}
    last_error = None
    
    async with httpx.AsyncClient(timeout=15.0) as client:
        for ep in endpoints:
            try:
                resp = await client.post(ep["url"], json=body)
                if resp.status_code == 429:
                    ep["cooldown"] = time.monotonic() + 30
                    continue
                data = resp.json()
                if "error" in data:
                    last_error = data["error"].get("message", str(data["error"]))
                    ep["cooldown"] = time.monotonic() + 5
                    continue
                result = data.get("result")
                if method in readonly_methods and result is not None:
                    _cache.set(method, params, result, ttl)
                return {"ok": True, "result": result, "endpoint": ep["name"], "cached": False}
            except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError) as e:
                last_error = str(e)
                ep["cooldown"] = time.monotonic() + 10
                continue
    
    return {"ok": False, "error": last_error or "All endpoints failed", "endpoint": None}

# ── FastMCP Server ──────────────────────────────────────────────────
mcp = FastMCP(
    "Solana Private",
    instructions="""
Private Solana RPC MCP server. Multi-endpoint fallback chain: Helius → Solana Labs → Lava → ZAN.
All read calls are cached (10-60s TTL). Rate limited per endpoint.
Access: Tailscale SSH only (private, dev backend).
"""
)

# ── Tools ───────────────────────────────────────────────────────────

@mcp.tool()
async def sol_health() -> dict:
    """Check health of all RPC endpoints. Returns which endpoint is active and response times."""
    results = {}
    async with httpx.AsyncClient(timeout=5.0) as client:
        for ep in RPC_ENDPOINTS:
            if ep["url"] is None:
                continue
            try:
                t0 = time.monotonic()
                resp = await client.post(ep["url"], json={"jsonrpc":"2.0","id":1,"method":"getHealth"})
                elapsed = time.monotonic() - t0
                results[ep["name"]] = {
                    "status": "ok" if resp.status_code == 200 else f"http_{resp.status_code}",
                    "latency_ms": round(elapsed * 1000),
                    "url": ep["url"][:60] + "...",
                }
            except Exception as e:
                results[ep["name"]] = {"status": "error", "error": str(e)[:100]}
    active = next((e["name"] for e in RPC_ENDPOINTS if e["url"] and results.get(e["name"], {}).get("status") == "ok"), None)
    return {"endpoints": results, "active": active, "total_endpoints": len(results)}

@mcp.tool()
async def sol_getTransaction(signature: str) -> dict:
    """Get full transaction details by signature. Returns parsed instructions, accounts, logs, and meta."""
    return await rpc_call("getTransaction", [signature, {"maxSupportedTransactionVersion": 0, "encoding": "jsonParsed"}], ttl=60)

@mcp.tool()
async def sol_getAccountInfo(address: str) -> dict:
    """Get account info for a Solana address. Returns lamports, owner, executable status, and data."""
    return await rpc_call("getAccountInfo", [address, {"encoding": "jsonParsed"}], ttl=15)

@mcp.tool()
async def sol_getBalance(address: str) -> dict:
    """Get SOL balance in lamports for an address."""
    return await rpc_call("getBalance", [address], ttl=15)

@mcp.tool()
async def sol_getTokenAccounts(address: str) -> dict:
    """Get all SPL token accounts owned by an address. Returns mint, amount, decimals for each token."""
    return await rpc_call("getTokenAccountsByOwner", [
        address,
        {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
        {"encoding": "jsonParsed"}
    ], ttl=15)

@mcp.tool()
async def sol_getBlock(slot: int) -> dict:
    """Get block details by slot number. Returns transactions, blockhash, parent slot, block time."""
    return await rpc_call("getBlock", [slot, {"maxSupportedTransactionVersion": 0, "encoding": "jsonParsed", "transactionDetails": "full", "rewards": False}], ttl=120)

@mcp.tool()
async def sol_getSignaturesForAddress(address: str, limit: int = 20, before: str = None) -> dict:
    """Get recent transaction signatures for an address. Returns signature, slot, blockTime, err status."""
    params = [address, {"limit": min(limit, 100)}]
    if before:
        params[1]["before"] = before
    return await rpc_call("getSignaturesForAddress", params, ttl=10)

@mcp.tool()
async def sol_getMultipleAccounts(addresses: list[str]) -> dict:
    """Get account info for multiple addresses in a single call. Max 100 addresses."""
    return await rpc_call("getMultipleAccounts", [addresses[:100], {"encoding": "jsonParsed"}], ttl=15)

@mcp.tool()
async def sol_getProgramAccounts(program_id: str, filters: list = None) -> dict:
    """Get all accounts owned by a program. Optional filters for data size or memcmp."""
    params = [program_id, {"encoding": "jsonParsed", "filters": filters or []}]
    return await rpc_call("getProgramAccounts", params, ttl=30)

@mcp.tool()
async def sol_getTokenLargestAccounts(mint: str) -> dict:
    """Get the 20 largest token accounts for a mint address."""
    return await rpc_call("getTokenLargestAccounts", [mint], ttl=30)

@mcp.tool()
async def sol_getTokenSupply(mint: str) -> dict:
    """Get the total supply of a token by mint address."""
    return await rpc_call("getTokenSupply", [mint], ttl=30)

@mcp.tool()
async def sol_getRecentBlockhash() -> dict:
    """Get a recent blockhash (for transaction building). Short TTL — always fresh."""
    return await rpc_call("getLatestBlockhash", [{"commitment": "finalized"}], ttl=0)

@mcp.tool()
async def sol_getSlot() -> dict:
    """Get the current slot number."""
    return await rpc_call("getSlot", [], ttl=0)

@mcp.tool()
async def sol_getEpochInfo() -> dict:
    """Get current epoch info: epoch, slotIndex, slotsInEpoch, absoluteSlot, blockHeight."""
    return await rpc_call("getEpochInfo", [], ttl=30)

@mcp.tool()
async def sol_simulateTransaction(tx_base64: str) -> dict:
    """Simulate a transaction without submitting. Returns logs, units consumed, and error status."""
    return await rpc_call("simulateTransaction", [tx_base64, {"encoding": "base64", "commitment": "processed"}], ttl=0)

@mcp.tool()
async def sol_getTokenPrice(mint: str) -> dict:
    """Get token price from Jupiter API (USD). Returns price, 24h change, volume. Not an on-chain RPC call."""
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(f"https://api.jup.ag/price/v2?ids={mint}")
            if resp.status_code == 200:
                data = resp.json()
                token_data = data.get("data", {}).get(mint, {})
                return {"ok": True, "mint": mint, "price_usd": token_data.get("price"), "price_change_24h": token_data.get("extra", {}).get("priceChange24h"), "endpoint": "jupiter"}
    except Exception:
        pass
    return {"ok": False, "error": "Jupiter price fetch failed", "mint": mint}

# ── Main ────────────────────────────────────────────────────────────
def main():
    mcp.run()

if __name__ == "__main__":
    main()

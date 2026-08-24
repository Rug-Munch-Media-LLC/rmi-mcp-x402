#!/usr/bin/env python3
"""
RugMunch Data MCP Server — Fleet-wide blockchain intelligence.
Solana + EVM + Cross-chain entity resolution + DuckDB analytics.
Deployed on Talos, accessible via Tailscale SSH only.
"""

import os, time, asyncio, hashlib, json
from typing import Optional, Any
from collections import OrderedDict
import httpx
import duckdb
import asyncpg
from fastmcp import FastMCP

# ── Config ────────────────────────────────────────────────────────
_env_file = os.path.join(os.path.dirname(__file__), '.env.helius')
if os.path.exists(_env_file):
    with open(_env_file) as f:
        for line in f:
            if '=' in line:
                k, v = line.strip().split('=', 1)
                os.environ.setdefault(k, v)

HELIUS_KEY = os.getenv("HELIUS_API_KEY", "")
PRY_API_URL = os.getenv("PRY_API_URL", "http://127.0.0.1:8005")
HELIUS_URL = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_KEY}" if HELIUS_KEY else None

# ── RPC Endpoints ─────────────────────────────────────────────────
SOLANA_RPC = [
    {"name":"helius","url":HELIUS_URL,"rate":50,"cooldown":0},
    {"name":"solana-labs","url":"https://api.mainnet-beta.solana.com","rate":10,"cooldown":0},
    {"name":"lava","url":"https://solana.lava.build","rate":20,"cooldown":0},
    {"name":"zan","url":"https://api.zan.top/solana-mainnet","rate":20,"cooldown":0},
]

EVM_RPC = {
    "ethereum": [
        {"name":"drpc","url":"https://eth.drpc.org","rate":30,"cooldown":0},
        {"name":"publicnode","url":"https://ethereum-rpc.publicnode.com","rate":20,"cooldown":0},
        {"name":"llamarpc","url":"https://eth.llamarpc.com","rate":20,"cooldown":0},
    ],
    "base": [
        {"name":"drpc","url":"https://base.drpc.org","rate":30,"cooldown":0},
        {"name":"publicnode","url":"https://base-rpc.publicnode.com","rate":20,"cooldown":0},
        {"name":"llamarpc","url":"https://base.llamarpc.com","rate":20,"cooldown":0},
    ],
    "arbitrum": [
        {"name":"drpc","url":"https://arbitrum.drpc.org","rate":30,"cooldown":0},
        {"name":"publicnode","url":"https://arbitrum-one-rpc.publicnode.com","rate":20,"cooldown":0},
        {"name":"llamarpc","url":"https://arbitrum.llamarpc.com","rate":20,"cooldown":0},
    ],
}

SOL_RPC = [e for e in SOLANA_RPC if e["url"]]
EVM_CHAINS = list(EVM_RPC.keys())

# ── Rate Limiter ──────────────────────────────────────────────────
class RL:
    def __init__(self): self.w: dict[str,list[float]] = {}
    def ok(self, ep: str, mx: float) -> bool:
        n = time.monotonic()
        w = [t for t in self.w.get(ep,[]) if n-t<1.0]
        self.w[ep] = w
        if len(w)>=mx: return False
        w.append(n); return True
_rl = RL()

# ── Cache ─────────────────────────────────────────────────────────
class Cache:
    def __init__(self, mx=500):
        self.s: OrderedDict[str,tuple[Any,float]] = OrderedDict()
        self.mx = mx
    def _k(self, m:str, p:Any) -> str:
        return hashlib.sha256(f"{m}:{json.dumps(p,sort_keys=True)}".encode()).hexdigest()[:16]
    def get(self, m:str, p:Any, ttl:int=30) -> Optional[Any]:
        k=self._k(m,p)
        if k in self.s:
            v,e=self.s[k]
            if time.time()<e: self.s.move_to_end(k); return v
            del self.s[k]
        return None
    def set(self, m:str, p:Any, v:Any, ttl:int=30):
        k=self._k(m,p)
        if k in self.s: self.s.move_to_end(k)
        else:
            if len(self.s)>=self.mx: self.s.popitem(last=False)
        self.s[k]=(v,time.time()+ttl)
_cache = Cache()

# ── RPC Engine ────────────────────────────────────────────────────
def _healthy(endpoints):
    now=time.monotonic()
    out=[]
    for ep in endpoints:
        if now<ep["cooldown"]: continue
        if _rl.ok(ep["name"],ep["rate"]): out.append(ep)
    if not out:
        for ep in endpoints: ep["cooldown"]=0
        out=[e for e in endpoints]
    return out

async def _rpc_call(endpoints, method:str, params:list=None, ttl:int=10) -> dict:
    readonly = {"getTransaction","getAccountInfo","getBalance","getTokenAccountsByOwner",
                "getBlock","getSignaturesForAddress","getMultipleAccounts","getProgramAccounts",
                "getTokenLargestAccounts","getTokenSupply","getEpochInfo","getSlot","getHealth",
                "eth_getBlockByNumber","eth_getTransactionByHash","eth_getBalance","eth_getBlockByHash",
                "eth_call","eth_getTransactionReceipt","eth_getLogs","eth_blockNumber","eth_chainId"}
    if method in readonly:
        c=_cache.get(method,params,ttl)
        if c is not None: return {"ok":True,"result":c,"endpoint":"cache","cached":True}
    eps=_healthy(endpoints)
    if not eps: return {"ok":False,"error":"No healthy endpoints","endpoint":None}
    body={"jsonrpc":"2.0","id":1,"method":method,"params":params or []}
    lerr=None
    async with httpx.AsyncClient(timeout=15.0) as cl:
        for ep in eps:
            try:
                r=await cl.post(ep["url"],json=body)
                if r.status_code==429: ep["cooldown"]=time.monotonic()+30; continue
                d=r.json()
                if "error" in d: lerr=d["error"].get("message",str(d)); ep["cooldown"]=time.monotonic()+5; continue
                result=d.get("result")
                if method in readonly and result is not None: _cache.set(method,params,result,ttl)
                return {"ok":True,"result":result,"endpoint":ep["name"],"cached":False}
            except Exception as e: lerr=str(e); ep["cooldown"]=time.monotonic()+10; continue
    return {"ok":False,"error":lerr or "All failed","endpoint":None}

# ── FastMCP Server ────────────────────────────────────────────────
mcp = FastMCP(
    "RugMunch Data",
    instructions="""
Fleet-wide blockchain intelligence MCP server. 
Solana: 4-endpoint RPC fallback (Helius→Labs→Lava→ZAN), caching, rate limiting.
EVM: 3-endpoint RPC per chain (DRPC→PublicNode→LlamaRPC).
Cross-chain: unified wallet profiling, entity resolution, risk scoring.
Private. Tailscale SSH access only.
"""
)

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
                t0=time.monotonic()
                r=await cl.post(ep["url"],json={"jsonrpc":"2.0","id":1,"method":"getHealth"})
                ms=(time.monotonic()-t0)*1000
                results[ep["name"]]={"status":"ok","latency_ms":round(ms)}
            except Exception as e:
                results[ep["name"]]={"status":"error","error":str(e)[:100]}
    active=next((n for n,r in results.items() if r["status"]=="ok"),None)
    return {"chain":"solana","endpoints":results,"active":active}

@mcp.tool()
async def sol_getTransaction(signature: str) -> dict:
    """Full Solana transaction details by signature (parsed instructions, accounts, logs)."""
    return await _rpc_call(SOL_RPC,"getTransaction",[signature,{"maxSupportedTransactionVersion":0,"encoding":"jsonParsed"}],ttl=60)

@mcp.tool()
async def sol_getAccountInfo(address: str) -> dict:
    """Solana account info: lamports, owner, executable, data."""
    return await _rpc_call(SOL_RPC,"getAccountInfo",[address,{"encoding":"jsonParsed"}],ttl=15)

@mcp.tool()
async def sol_getBalance(address: str) -> dict:
    """SOL balance in lamports for an address."""
    return await _rpc_call(SOL_RPC,"getBalance",[address],ttl=10)

@mcp.tool()
async def sol_getTokenAccounts(address: str) -> dict:
    """All SPL token accounts owned by an address (mint, amount, decimals)."""
    return await _rpc_call(SOL_RPC,"getTokenAccountsByOwner",[address,{"programId":"TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},{"encoding":"jsonParsed"}],ttl=15)

@mcp.tool()
async def sol_getBlock(slot: int) -> dict:
    """Solana block details by slot (transactions, blockhash, time)."""
    return await _rpc_call(SOL_RPC,"getBlock",[slot,{"maxSupportedTransactionVersion":0,"encoding":"jsonParsed","transactionDetails":"full","rewards":False}],ttl=120)

@mcp.tool()
async def sol_getSignaturesForAddress(address: str, limit: int = 20) -> dict:
    """Recent transaction signatures for a Solana address."""
    return await _rpc_call(SOL_RPC,"getSignaturesForAddress",[address,{"limit":min(limit,100)}],ttl=10)

@mcp.tool()
async def sol_getMultipleAccounts(addresses: list[str]) -> dict:
    """Batch account info for up to 100 Solana addresses."""
    return await _rpc_call(SOL_RPC,"getMultipleAccounts",[addresses[:100],{"encoding":"jsonParsed"}],ttl=15)

@mcp.tool()
async def sol_getTokenSupply(mint: str) -> dict:
    """Total supply of an SPL token by mint address."""
    return await _rpc_call(SOL_RPC,"getTokenSupply",[mint],ttl=30)

@mcp.tool()
async def sol_getTokenLargestAccounts(mint: str) -> dict:
    """Top 20 token holders for a mint address."""
    return await _rpc_call(SOL_RPC,"getTokenLargestAccounts",[mint],ttl=30)

@mcp.tool()
async def sol_getSlot() -> dict:
    """Current Solana slot number."""
    return await _rpc_call(SOL_RPC,"getSlot",[],ttl=0)

@mcp.tool()
async def sol_getEpochInfo() -> dict:
    """Current Solana epoch info: epoch, slot index, absolute slot, block height."""
    return await _rpc_call(SOL_RPC,"getEpochInfo",[],ttl=30)

@mcp.tool()
async def sol_getProgramAccounts(program_id: str) -> dict:
    """All accounts owned by a Solana program."""
    return await _rpc_call(SOL_RPC,"getProgramAccounts",[program_id,{"encoding":"jsonParsed","filters":[]}],ttl=30)

@mcp.tool()
async def sol_getTokenPrice(mint: str) -> dict:
    """Token price from Jupiter API (USD)."""
    try:
        async with httpx.AsyncClient(timeout=8.0) as cl:
            r=await cl.get(f"https://api.jup.ag/price/v2?ids={mint}")
            if r.status_code==200:
                d=r.json()
                t=d.get("data",{}).get(mint,{})
                return {"ok":True,"mint":mint,"price_usd":t.get("price"),"endpoint":"jupiter"}
    except: pass
    return {"ok":False,"error":"Jupiter fetch failed","mint":mint}

@mcp.tool()
async def sol_simulateTransaction(tx_base64: str) -> dict:
    """Simulate Solana transaction without submitting."""
    return await _rpc_call(SOL_RPC,"simulateTransaction",[tx_base64,{"encoding":"base64","commitment":"processed"}],ttl=0)

@mcp.tool()
async def sol_getRecentBlockhash() -> dict:
    """Recent Solana blockhash for transaction building."""
    return await _rpc_call(SOL_RPC,"getLatestBlockhash",[{"commitment":"finalized"}],ttl=0)

# ═══════════════════════════════════════════════════════════════════
# EVM TOOLS
# ═══════════════════════════════════════════════════════════════════

@mcp.tool()
async def evm_health(chain: str = "ethereum") -> dict:
    """Health check for EVM RPC endpoints. Chain: ethereum, base, or arbitrum."""
    endpoints = EVM_RPC.get(chain, [])
    if not endpoints: return {"ok":False,"error":f"Unknown chain: {chain}"}
    results = {}
    async with httpx.AsyncClient(timeout=5.0) as cl:
        for ep in endpoints:
            try:
                t0=time.monotonic()
                r=await cl.post(ep["url"],json={"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]})
                ms=(time.monotonic()-t0)*1000
                results[ep["name"]]={"status":"ok","latency_ms":round(ms),"block":int(r.json().get("result","0x0"),16)}
            except Exception as e:
                results[ep["name"]]={"status":"error","error":str(e)[:100]}
    active=next((n for n,r in results.items() if r["status"]=="ok"),None)
    return {"chain":chain,"endpoints":results,"active":active}

@mcp.tool()
async def evm_getBlock(chain: str = "ethereum", block: str = "latest") -> dict:
    """Get EVM block by number or 'latest'. Chain: ethereum, base, arbitrum."""
    endpoints = EVM_RPC.get(chain, [])
    if not endpoints: return {"ok":False,"error":f"Unknown chain: {chain}"}
    if block != "latest": block = hex(int(block))
    return await _rpc_call(endpoints,"eth_getBlockByNumber",[block,True],ttl=30)

@mcp.tool()
async def evm_getTransaction(chain: str, tx_hash: str) -> dict:
    """Get EVM transaction by hash. Chain: ethereum, base, arbitrum."""
    endpoints = EVM_RPC.get(chain, [])
    if not endpoints: return {"ok":False,"error":f"Unknown chain: {chain}"}
    return await _rpc_call(endpoints,"eth_getTransactionByHash",[tx_hash],ttl=60)

@mcp.tool()
async def evm_getBalance(chain: str, address: str) -> dict:
    """Get ETH/native token balance for an address in wei."""
    endpoints = EVM_RPC.get(chain, [])
    if not endpoints: return {"ok":False,"error":f"Unknown chain: {chain}"}
    return await _rpc_call(endpoints,"eth_getBalance",[address,"latest"],ttl=10)

@mcp.tool()
async def evm_getTransactionReceipt(chain: str, tx_hash: str) -> dict:
    """Get EVM transaction receipt (logs, gas used, status)."""
    endpoints = EVM_RPC.get(chain, [])
    if not endpoints: return {"ok":False,"error":f"Unknown chain: {chain}"}
    return await _rpc_call(endpoints,"eth_getTransactionReceipt",[tx_hash],ttl=60)

@mcp.tool()
async def evm_blockNumber(chain: str = "ethereum") -> dict:
    """Get current block number for an EVM chain."""
    endpoints = EVM_RPC.get(chain, [])
    if not endpoints: return {"ok":False,"error":f"Unknown chain: {chain}"}
    return await _rpc_call(endpoints,"eth_blockNumber",[],ttl=0)

@mcp.tool()
async def evm_getLogs(chain: str, address: str, fromBlock: str = "0x0", toBlock: str = "latest") -> dict:
    """Get event logs for an EVM contract address."""
    endpoints = EVM_RPC.get(chain, [])
    if not endpoints: return {"ok":False,"error":f"Unknown chain: {chain}"}
    return await _rpc_call(endpoints,"eth_getLogs",[{"address":address,"fromBlock":fromBlock,"toBlock":toBlock}],ttl=30)

@mcp.tool()
async def evm_call(chain: str, to: str, data: str) -> dict:
    """Execute a read-only contract call (eth_call). Chain: ethereum, base, arbitrum."""
    endpoints = EVM_RPC.get(chain, [])
    if not endpoints: return {"ok":False,"error":f"Unknown chain: {chain}"}
    return await _rpc_call(endpoints,"eth_call",[{"to":to,"data":data},"latest"],ttl=5)

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
                r=await cl.post(ep["url"],json={"jsonrpc":"2.0","id":1,"method":"getHealth"})
                if r.json().get("result")=="ok": results["solana"]="ok"; break
            except: pass
        if "solana" not in results: results["solana"]="down"
        # EVM
        for chain, eps in EVM_RPC.items():
            for ep in eps:
                try:
                    r=await cl.post(ep["url"],json={"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]})
                    b=int(r.json().get("result","0x0"),16)
                    results[chain]=f"ok (block {b})"; break
                except: pass
            if chain not in results: results[chain]="down"
    return {"chains":results,"timestamp":time.time()}

@mcp.tool()
async def xchain_profile(address: str) -> dict:
    """Cross-chain wallet profile. Queries Solana + Ethereum + Base + Arbitrum for the SAME address pattern."""
    # Check Solana (base58 address)
    sol_info = None
    if len(address) <= 44:
        r = await _rpc_call(SOL_RPC,"getBalance",[address],ttl=10)
        sol_info = {"balance_lamports":r.get("result"),"endpoint":r.get("endpoint")}
    
    # Check EVM (hex address)
    evm_info = {}
    if address.startswith("0x") or len(address) == 42:
        for chain in ["ethereum","base","arbitrum"]:
            eps = EVM_RPC.get(chain,[])
            r = await _rpc_call(eps,"eth_getBalance",[address,"latest"],ttl=10)
            bal = r.get("result")
            if bal and bal != "0x0":
                evm_info[chain] = {"balance_wei":int(bal,16),"endpoint":r.get("endpoint")}
    
    return {
        "address": address,
        "solana": sol_info or "no activity",
        "evm": evm_info or "no activity",
        "profile_type": "solana" if sol_info else "evm" if evm_info else "unknown",
    }


# ── Configuration ─────────────────────────────────────────────────
HF_HUB_TOKEN = os.getenv("HF_HUB_TOKEN", "")
HF_REPO = os.getenv("HF_REPO_ID", "rugmunchmedia/solana-archives")
HF_BASE_URL = f"https://huggingface.co/datasets/{HF_REPO}/resolve/main"

PG_HOST = os.getenv("POSTGRES_HOST", "127.0.0.1")
PG_PORT = int(os.getenv("POSTGRES_PORT", "5433"))
PG_DB = os.getenv("POSTGRES_DB", "entity_labels")
PG_USER = os.getenv("POSTGRES_USER", "labeler")
PG_PASS = os.getenv("POSTGRES_PASSWORD", "")

# ── DuckDB httpfs initialization ─────────────────────────────────
_duck_initialized = False

def _get_duckdb():
    """Lazy-init DuckDB with httpfs for HF Hub remote queries."""
    global _duck_initialized
    import duckdb
    db = duckdb.connect()
    if not _duck_initialized:
        db.execute("INSTALL httpfs; LOAD httpfs;")
        if HF_HUB_TOKEN:
            db.execute(f"""
                CREATE SECRET hf_token (
                    TYPE HTTP,
                    EXTRA_HTTP_HEADERS MAP {{
                        'Authorization': 'Bearer {HF_HUB_TOKEN}'
                    }}
                );
            """)
        _duck_initialized = True
    return db

async def _get_pg():
    """Lazy-init asyncpg connection pool."""
    if not PG_PASS:
        return None
    try:
        conn = await asyncpg.connect(
            host=PG_HOST, port=PG_PORT,
            database=PG_DB, user=PG_USER,
            password=PG_PASS, timeout=10
        )
        return conn
    except Exception as e:
        return None

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
            rows = await conn.fetch("""
                SELECT wl.label_tag, wl.confidence_score, wl.chain, wl.source,
                       ep.entity_name, ep.entity_type
                FROM wallet_labels wl
                LEFT JOIN entity_profiles ep ON wl.entity_id = ep.id
                WHERE wl.wallet_address = $1
                ORDER BY wl.confidence_score DESC
                LIMIT 20
            """, address)
            for row in rows:
                result["entity_tags"].append({
                    "label": row["label_tag"],
                    "confidence": float(row["confidence_score"]),
                    "chain": row["chain"],
                    "source": row["source"],
                    "entity": row["entity_name"],
                    "entity_type": row["entity_type"],
                })
            await conn.close()
        except Exception as e:
            result["entity_tags"] = [{"error": str(e)[:200]}]

    # 4. DuckDB historical archive query (if HF Hub configured)
    if HF_HUB_TOKEN:
        try:
            db = _get_duckdb()
            chain_dir = "solana" if len(address) <= 44 else "evm"
            archives_url = f"{HF_BASE_URL}/{chain_dir}/"

            query = f"""
                SELECT * 
                FROM read_json_auto('{archives_url}**/*.jsonl.gz', union_by_name=true)
                WHERE 
                    (LOWER(from_address) = LOWER('{address}') OR LOWER(to_address) = LOWER('{address}'))
                LIMIT 100
            """
            try:
                df = db.execute(query).fetchdf()
                if len(df) > 0:
                    result["historical_patterns"] = {
                        "total_matches": len(df),
                        "first_seen": str(df["block_time"].min()) if "block_time" in df.columns else None,
                        "last_seen": str(df["block_time"].max()) if "block_time" in df.columns else None,
                        "unique_counterparties": int(df["from_address"].nunique()) if "from_address" in df.columns else 0,
                        "sample_tx": df.head(5).to_dict(orient="records") if len(df) > 0 else [],
                    }
            except Exception as e:
                result["historical_patterns"] = {"error": str(e)[:200], "note": "Archives may not be populated yet — run the HF Space indexer first"}
        except Exception as e:
            result["historical_patterns"] = {"error": str(e)[:200]}

    # 5. Risk signals
    if result["live_balances"].get("solana", {}).get("sol", 0) > 0:
        # Check SPL token holdings for scam tokens
        r = await _rpc_call(SOL_RPC, "getTokenAccountsByOwner", [
            address,
            {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
            {"encoding": "jsonParsed"}
        ], ttl=10)
        if r.get("ok"):
            token_count = len(r["result"].get("value", []))
            if token_count > 500:
                result["risk_signals"].append("high_token_count")
            if token_count < 1:
                result["risk_signals"].append("no_tokens")

    if result["entity_tags"]:
        scam_tags = [t for t in result["entity_tags"] if t["label"] in ["scammer", "mixer", "phisher"]]
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
        rows = await conn.fetch("""
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
        """, f"%{query}%")

        results = []
        for row in rows:
            results.append({
                "address": row["wallet_address"],
                "label": row["label_tag"],
                "confidence": float(row["confidence_score"]),
                "chain": row["chain"],
                "entity": row["entity_name"],
                "type": row["entity_type"],
                "description": row["description"],
            })

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
                "fields": ["block_number", "block_time", "tx_hash", "from_address", "to_address", "token_mint", "amount", "program_id", "instruction_type", "slot"],
                "note": "Indexed via SQD Network Data Lake. Free, no rate limits.",
            },
            "evm": {
                "networks": ["ethereum-mainnet", "base-mainnet", "arbitrum-mainnet"],
                "fields": ["block_number", "block_time", "tx_hash", "from_address", "to_address", "value_wei", "gas_price_wei", "gas_used", "method_id", "chain"],
                "note": "Indexed via DRPC + PublicNode + LlamaRPC fallback chain.",
            },
        },
        "query_examples": {
            "wallet_history": "SELECT * FROM read_json_auto('https://huggingface.co/datasets/rugmunchmedia/solana-archives/resolve/main/solana/solana-mainnet/*.jsonl.gz', union_by_name=true) WHERE from_address = 'YOUR_ADDRESS' LIMIT 100",
            "token_activity": "SELECT COUNT(*) AS tx_count, SUM(CAST(amount AS DOUBLE)) AS volume FROM read_json_auto('...') WHERE token_mint = 'YOUR_MINT' AND block_time > '2026-01-01'",
            "cross_chain": "SELECT 'solana' AS chain, COUNT(*) AS count FROM read_json_auto('...solana...') UNION ALL SELECT 'ethereum', COUNT(*) FROM read_json_auto('...evm/ethereum...')",
        },
    }


# ── Main ──────────────────────────────────────────────────────────
def main():
    mcp.run()


# ── Pry web scraping (x402-gated data layer) ─────────────────────────────────

@mcp.tool()
async def web_scrape(
    url: str,
    formats: Optional[list[str]] = None,
    bypass_cloudflare: bool = True,
    js_render: bool = True,
) -> dict:
    """Scrape any webpage via the self-hosted Pry engine and return clean,
    LLM-ready markdown or JSON. Adaptive 12-tier anti-bot escalation handles
    Cloudflare/DataDome/Akamai automatically when bypass_cloudflare is set.

    Args:
        url: Absolute http(s) URL of the page to scrape.
        formats: Output formats, e.g. ["markdown"] or ["json"]. Defaults to markdown.
        bypass_cloudflare: Enable adaptive anti-bot escalation (default true).
        js_render: Allow browser-tier rendering for JS-heavy pages (default true).

    Returns:
        {"url": ..., "status": "ok"|"error", "content": <markdown/json>, "method": <tier used>}
    """
    payload: dict = {
        "urls": [url],
        "output_format": (formats or ["markdown"])[0],
    }
    if not bypass_cloudflare:
        payload["max_tier"] = "direct"
    if not js_render:
        payload["disable_browser"] = True
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(f"{PRY_API_URL}/v1/scrape", json=payload)
        resp.raise_for_status()
        return resp.json()

if __name__ == "__main__":
    main()

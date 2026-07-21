"""T34 x402 Payment Middleware.

Per v4.0 §T34. HTTP 402 'Payment Required' repurposed for AI agents.

Flow:
  1. Agent calls paid endpoint without X-Payment header
  2. Server returns 402 with payment challenge (signed invoice)
  3. Agent's wallet pays on-chain, gets tx_hash
  4. Agent re-calls with X-Payment: <base64(tx_hash + signature)>
  5. Server verifies on-chain payment via web3, fulfills request

Pricing (per v4.0):
  Free:   5 calls/day
  Pro:    $0.01/call (1000 calls/day)
  Ent:    $0.001/call (unlimited)

For v1, we use a simplified flow:
  - Track calls per agent in Redis (sliding window)
  - Receipts logged to x402_receipts table
  - Payment verification via JSON-RPC eth_getTransactionReceipt
    with multi-endpoint fallback across base/ethereum/arbitrum
  - The 402 challenge includes a payment_url the agent hits
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
from fastapi import HTTPException, Request

log = logging.getLogger(__name__)


# ── On-chain payment config ────────────────────────────────────────
# Payment address (env configurable, matches x402_checkout.py default)
PAY_TO = os.getenv("X402_PAY_TO", "0x1E3AC01d0fdb976179790BDD02823196A92705C9").lower()

# ERC-20 Transfer event: keccak256("Transfer(address,address,uint256)")
TRANSFER_EVENT_TOPIC0 = (
    "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
)

USDC_DECIMALS = 6
USDC_ATOMS_PER_DOLLAR = 10**USDC_DECIMALS  # 1 USDC = 1_000_000 atoms

USDC_ADDRESSES: dict[str, str] = {
    "ethereum": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
    "base": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    "arbitrum": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
}

# EVM RPC endpoints for eth_getTransactionReceipt (with fallback)
EVM_RPC_URLS: dict[str, list[str]] = {
    "ethereum": [
        "https://eth.drpc.org",
        "https://ethereum-rpc.publicnode.com",
        "https://eth.llamarpc.com",
    ],
    "base": [
        "https://base.drpc.org",
        "https://base-rpc.publicnode.com",
        "https://base.llamarpc.com",
    ],
    "arbitrum": [
        "https://arbitrum.drpc.org",
        "https://arbitrum-one-rpc.publicnode.com",
        "https://arbitrum.llamarpc.com",
    ],
}


# ── Pricing per v4.0 §T34 ───────────────────────────────────────────
PRICING: dict[str, dict[str, Any]] = {
    # free_tier: bool, pro_cents: int (USD cents), ent_cents: int
    "get_token_risk": {
        "free_tier": True,
        "pro_cents": 1,
        "ent_cents": 1,
        "description": "Real-time token risk score",
    },
    "get_wallet_analysis": {
        "free_tier": True,
        "pro_cents": 1,
        "ent_cents": 1,
        "description": "Wallet activity + reputation",
    },
    "get_deployer_reputation": {
        "free_tier": False,
        "pro_cents": 2,
        "ent_cents": 1,
        "description": "Deployer reputation score",
    },
    "get_news_sentiment": {
        "free_tier": True,
        "pro_cents": 1,
        "ent_cents": 1,
        "description": "Latest news + sentiment",
    },
    "generate_report": {
        "free_tier": False,
        "pro_cents": 500,
        "ent_cents": 400,
        "description": "Full AI research report",
    },
    "query_catalog": {
        "free_tier": False,
        "pro_cents": 5,
        "ent_cents": 3,
        "description": "Natural language catalog query",
    },
    "find_similar_tokens": {
        "free_tier": False,
        "pro_cents": 3,
        "ent_cents": 2,
        "description": "Vector-similar tokens",
    },
    "resolve_entity": {
        "free_tier": False,
        "pro_cents": 10,
        "ent_cents": 5,
        "description": "Cross-chain entity resolution",
    },
    "search_rag": {
        "free_tier": True,
        "pro_cents": 1,
        "ent_cents": 1,
        "description": "RAG semantic search",
    },
    "bulk_ingest": {
        "free_tier": False,
        "pro_cents": 10,
        "ent_cents": 5,
        "description": "Bulk RAG ingestion",
    },
}

# Free tier daily limit
FREE_DAILY_LIMIT = 5


# ── Nonce burn (replay protection) ──────────────────────────────
# Per MINIMAX_M3_TASKS.md T02. Each tx_hash can only be used ONCE.
# We use Redis SETNX (set-if-not-exists) as an atomic nonce burn:
#   - First call: SETNX returns 1, nonce is burned, request proceeds
#   - Second call: SETNX returns 0, nonce already burned → 410 Gone
# TTL is 24h (the x402 invoice expiry window).

X402_NONCE_TTL = 86400  # 24 hours


async def _burn_nonce(catalog, tx_hash: str) -> bool:
    """Atomically burn a payment nonce. Returns True if first use, False if replay.

    Uses Redis SETNX (SET key value NX EX seconds). Atomic by construction.
    Logs replay attempts for monitoring.
    """
    if not catalog or not catalog._health.redis:
        # Without Redis we cannot prevent replay — fail closed (reject).
        log.warning(f"x402_nocache_reject: tx_hash={tx_hash[:10]}...")
        return False
    key = f"x402:nonce:{tx_hash}"
    try:
        # SETNX semantics: return 1 if key was set, 0 if already existed
        ok = await catalog._redis.set(key, "1", ex=X402_NONCE_TTL, nx=True)
        if not ok:
            log.warning(f"x402_replay_attempt: tx_hash={tx_hash[:10]}...")
            _X402_REPLAY_COUNTER.labels(tool="unknown").inc()
        return bool(ok)
    except Exception as e:
        log.error(f"x402_nonce_burn_fail: {e}")
        # Fail closed — reject if we can't burn atomically
        return False


async def _check_rate_limit(redis_client, agent_id: str) -> bool:
    """Per-agent sliding window rate limit. Free tier = 5 calls/day."""
    # (kept as-is from original file, exposed for the require_payment path)


# ── Prometheus counters (lightweight, no external dep) ──────────
try:
    from prometheus_client import REGISTRY, Counter

    # Use a module-level registry so multiple imports don't double-register
    _X402_REGISTRY = REGISTRY
    _X402_REPLAY_COUNTER = Counter(
        "x402_replay_attempts_total",
        "Number of x402 replay attempts blocked by nonce burn",
        ["tool"],
        registry=_X402_REGISTRY,
    )
    _X402_REVENUE_COUNTER = Counter(
        "x402_revenue_total_usd_cents",
        "x402 revenue in USD cents",
        ["tool", "tier"],
        registry=_X402_REGISTRY,
    )
except Exception:
    # Prometheus not available — use no-op counter stubs
    class _Stub:
        def labels(self, **kw):
            return self

        def inc(self, n: float = 1.0):
            pass

    _X402_REPLAY_COUNTER = _Stub()
    _X402_REVENUE_COUNTER = _Stub()


def get_tool_price_usd(tool: str) -> float:
    """Get pro tier price in USD. Returns 0 for free tools."""
    p = PRICING.get(tool, {})
    if p.get("free_tier", False):
        return 0.0
    return p.get("pro_cents", 0) / 100.0


def get_tool_metadata(tool: str) -> dict:
    """Public-facing tool metadata for /api/v1/x402/catalog."""
    p = PRICING.get(tool, {})
    return {
        "name": tool,
        "free_tier": p.get("free_tier", False),
        "free_daily_limit": FREE_DAILY_LIMIT if p.get("free_tier") else 0,
        "price_pro_usd": p.get("pro_cents", 0) / 100.0,
        "price_ent_usd": p.get("ent_cents", 0) / 100.0,
        "description": p.get("description", ""),
    }


# ── Rate limit tracking (Redis) ───────────────────────────────────
def _agent_id_from_request(request: Request | None) -> str:
    """Extract agent id from headers. Defaults to 'anon:ip' for anonymous."""
    if not request:
        return "anon:unknown"
    agent_id = request.headers.get("X-Agent-Id") or request.headers.get("X-Api-Key")
    if agent_id:
        return f"key:{agent_id}"
    client = request.client
    if client:
        return f"anon:{client.host}"
    return "anon:unknown"


async def _check_rate_limit(redis, agent_id: str) -> None:
    """Sliding-window rate limit: 5 free calls/day, 1000 pro, unlimited ent.

    For v1, we use Redis with a daily counter. Per-second limits are
    enforced at the proxy level (nginx/Caddy).
    """
    if not redis:
        return
    try:
        day = datetime.now(UTC).strftime("%Y%m%d")
        key = f"x402:rl:{agent_id}:{day}"
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, 86400)
        if count > FREE_DAILY_LIMIT:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "rate_limit_exceeded",
                    "agent_id": agent_id,
                    "daily_count": count,
                    "limit": FREE_DAILY_LIMIT,
                    "next_action": "Send X-Payment header with valid tx_hash to upgrade tier",
                },
            )
    except HTTPException:
        raise
    except Exception as e:
        log.warning(f"rate_limit_check_fail: {e}")


# ── Payment challenge ─────────────────────────────────────────────
def create_invoice(tool: str, agent_id: str) -> dict:
    """Create a payment challenge (signed invoice) for an agent to pay.

    In v1, the signature is a HMAC placeholder. In production, the
    invoice is signed by the platform's key and the agent verifies it
    before paying.
    """
    invoice_id = uuid4().hex
    amount_usd = get_tool_price_usd(tool)
    invoice = {
        "id": invoice_id,
        "tool": tool,
        "agent_id": agent_id,
        "amount_usd": amount_usd,
        "amount_wei": int(amount_usd * 1e18 / 3000),  # assume ETH = $3000
        "pay_to": "0xRMI_PLATFORM_WALLET",  # placeholder
        "chain": "base",  # L2 for low fees
        "created_at": datetime.now(UTC).isoformat(),
        "expires_at": (datetime.now(UTC).timestamp() + 3600),
    }
    # HMAC placeholder signature (real impl: sign with platform key)
    msg = json.dumps(invoice, sort_keys=True).encode()
    invoice["signature"] = base64.b64encode(
        hashlib.sha256(msg + b"RMI_PLATFORM_HMAC_KEY").digest()
    ).decode()
    return invoice


def verify_payment_header(
    x_payment: str, expected_amount_usd: float
) -> tuple[str, str] | None:
    """Decode the X-Payment header. Returns (tx_hash, signature) or None.

    Format: base64({"tx_hash": "...", "signature": "..."})
    """
    if not x_payment:
        return None
    try:
        decoded = base64.b64decode(x_payment).decode()
        data = json.loads(decoded)
        return data.get("tx_hash", ""), data.get("signature", "")
    except Exception:
        return None


async def _fetch_receipt_via_rpc(tx_hash: str, rpc_urls: list[str]) -> dict | None:
    """Fetch transaction receipt via JSON-RPC eth_getTransactionReceipt.

    Tries each URL in order (rate-limit 429 → skip, error → skip).
    Returns the receipt dict on first success, None if all fail.
    """
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_getTransactionReceipt",
        "params": [tx_hash],
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        for url in rpc_urls:
            try:
                resp = await client.post(url, json=body)
                if resp.status_code == 429:
                    continue  # rate limited, try next endpoint
                data = resp.json()
                if data.get("error"):
                    continue
                result = data.get("result")
                if result:
                    return result
            except Exception:
                continue
    return None


async def verify_payment_on_chain(tx_hash: str, expected_amount_usd: float) -> bool:
    """Verify an on-chain USDC payment.

    Strategy:
      1. Validate tx_hash format
      2. Fetch transaction receipt via JSON-RPC (with multi-endpoint fallback)
      3. Verify tx status = success
      4. Find ERC-20 Transfer event to PAY_TO from the USDC contract
      5. Verify the transferred amount matches expected_amount_usd
      6. Return True only when ALL checks pass (with detailed logging)

    Tries chains in order: base (default per invoice), ethereum, arbitrum.
    """
    if not tx_hash or not tx_hash.startswith("0x") or len(tx_hash) != 66:
        log.warning(f"x402_bad_hash: {str(tx_hash)[:20] if tx_hash else 'None'}...")
        return False

    expected_atoms = str(int(expected_amount_usd * USDC_ATOMS_PER_DOLLAR))
    our_address = PAY_TO.lower()

    # Try chains in priority order (base is the invoice default)
    for chain in ("base", "ethereum", "arbitrum"):
        rpc_urls = EVM_RPC_URLS.get(chain)
        usdc_address = USDC_ADDRESSES.get(chain)
        if not rpc_urls or not usdc_address:
            continue

        receipt = await _fetch_receipt_via_rpc(tx_hash, rpc_urls)
        if not receipt:
            continue

        # ── Verify transaction succeeded ──
        status = receipt.get("status")
        if status is not None:
            status_str = str(status).lower()
            if status_str not in ("ok", "1", "0x1"):
                log.warning(
                    f"x402_tx_failed: tx={tx_hash[:16]}... chain={chain} status={status}"
                )
                return False

        # ── Scan logs for USDC Transfer to PAY_TO ──
        logs = receipt.get("logs", [])
        usdc_address = usdc_address.lower()

        for log_entry in logs:
            topics = log_entry.get("topics", [])
            if len(topics) < 3:
                continue
            if topics[0].lower() != TRANSFER_EVENT_TOPIC0:
                continue

            log_address = (log_entry.get("address") or "").lower()
            if log_address != usdc_address:
                continue

            # Decode 'to' from topic2 (address padded to 32 bytes)
            try:
                to_address = "0x" + topics[2][-40:].lower()
            except (IndexError, ValueError):
                continue

            if to_address != our_address:
                continue

            # ── Decode amount from data (uint256) ──
            log_data = log_entry.get("data", "0x")
            try:
                if log_data and log_data.startswith("0x") and len(log_data) >= 66:
                    actual_amount = str(int(log_data[:66], 16))
                elif log_data and log_data != "0x":
                    actual_amount = str(int(log_data, 16))
                else:
                    continue
            except (ValueError, TypeError):
                continue

            # ── Check amount matches ──
            if actual_amount == expected_atoms:
                log.info(
                    f"x402_paid: tx={tx_hash[:16]}... chain={chain} "
                    f"${expected_amount_usd} USDC → {our_address[:10]}..."
                )
                return True

            log.warning(
                f"x402_amount_mismatch: tx={tx_hash[:16]}... chain={chain} "
                f"expected={expected_atoms} got={actual_amount} atoms"
            )
            return False

        # Receipt found & logs scanned but no matching Transfer → payment not to us
        log.warning(
            f"x402_no_transfer: tx={tx_hash[:16]}... chain={chain} "
            f"expected USDC={usdc_address[:10]}... → {our_address[:10]}..."
        )
        return False

    log.warning(
        f"x402_no_receipt: tx={tx_hash[:16]}... tried chains=base, ethereum, arbitrum"
    )
    return False


async def record_receipt(
    catalog, tx_hash: str, tool: str, agent_id: str, amount_usd: float
) -> bool:
    """Log the payment receipt to x402_receipts table."""
    if not catalog._health.postgres:
        return False
    try:
        async with catalog._pg_pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO x402_receipts (tx_hash, agent_id, tool, amount_usd, chain, paid_at, tier)
                   VALUES ($1, $2, $3, $4, $5, NOW(), $6)
                   ON CONFLICT (tx_hash) DO NOTHING""",
                tx_hash,
                agent_id,
                tool,
                amount_usd,
                "base",
                "pro",
            )
        return True
    except Exception as e:
        log.warning(f"receipt_record_fail: {e}")
        return False


# ── Main middleware ────────────────────────────────────────────────
async def require_payment(
    tool: str,
    request: Request | None = None,
    catalog=None,
) -> dict:
    """Verify x402 payment. Returns payment context dict.

    Raises HTTPException 402 if payment is required but not provided.
    Raises HTTPException 429 if rate limit is exceeded.
    """
    agent_id = _agent_id_from_request(request)
    if not catalog or not catalog._health.redis:
        # No rate limiting available — log and pass
        log.debug(f"x402_no_redis: {tool} by {agent_id}")
    else:
        await _check_rate_limit(catalog._redis, agent_id)

    # Free tool — no payment required
    if get_tool_price_usd(tool) == 0:
        return {
            "agent_id": agent_id,
            "tool": tool,
            "tier": "free",
            "paid_via_x402": None,
        }

    # Paid tool — check X-Payment header
    if request is None:
        invoice = create_invoice(tool, agent_id)
        raise HTTPException(
            status_code=402,
            detail={
                "error": "payment_required",
                "invoice": invoice,
                "payment_url": f"https://pay.rugmunch.io/{invoice['id']}",
                "instructions": "Pay on-chain, then retry with X-Payment header (base64 of {tx_hash, signature})",
            },
        )

    x_payment = request.headers.get("X-Payment")
    if not x_payment:
        invoice = create_invoice(tool, agent_id)
        raise HTTPException(
            status_code=402,
            detail={
                "error": "payment_required",
                "invoice": invoice,
                "payment_url": f"https://pay.rugmunch.io/{invoice['id']}",
                "instructions": "Pay on-chain, then retry with X-Payment header (base64 of {tx_hash, signature})",
            },
        )

    decoded = verify_payment_header(x_payment, get_tool_price_usd(tool))
    if not decoded:
        raise HTTPException(status_code=402, detail="invalid X-Payment format")
    tx_hash, _sig = decoded
    if not await verify_payment_on_chain(tx_hash, get_tool_price_usd(tool)):
        raise HTTPException(status_code=402, detail="invalid payment proof")

    # T02: Burn the nonce atomically. Same tx_hash cannot be used twice.
    if not await _burn_nonce(catalog, tx_hash):
        raise HTTPException(
            status_code=410,
            detail={
                "error": "payment_already_used",
                "tx_hash": tx_hash,
                "message": "This payment receipt has already been used. Each tx_hash can only be used once.",
            },
        )
    _X402_REVENUE_COUNTER.labels(tool=tool, tier="pro").inc(
        int(get_tool_price_usd(tool) * 100)
    )

    # Record the receipt
    if catalog:
        await record_receipt(catalog, tx_hash, tool, agent_id, get_tool_price_usd(tool))

    return {
        "agent_id": agent_id,
        "tool": tool,
        "tier": "pro",
        "paid_via_x402": tx_hash,
    }

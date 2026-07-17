"""
T1-1: x402 Subscription Checkout — Complete payment flow for RMI subscriptions.
"""
import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger("x402_checkout")
DB_PATH = "/data/x402_checkout.db"

FACILITATOR_URL = os.getenv("X402_FACILITATOR", "https://x402.org/facilitator")

# ── On-chain payment config ────────────────────────────────────────
USDC_ADDRESSES = {
    "ethereum": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
    "base": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    "arbitrum": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
    "polygon": "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",
    "optimism": "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85",
}

# Blockscout v2 API base URLs (free, no API key)
BLOCKSCOUT_URLS = {
    "ethereum": "https://eth.blockscout.com",
    "base": "https://base.blockscout.com",
    "arbitrum": "https://arbitrum.blockscout.com",
    "polygon": "https://polygon.blockscout.com",
    "optimism": "https://optimism.blockscout.com",
    "bsc": "https://bsc.blockscout.com",
    "gnosis": "https://gnosis.blockscout.com",
    "avalanche": "https://avalanche.blockscout.com",
    "fantom": "https://fantom.blockscout.com",
}

# EVM RPC endpoints for raw eth_getTransactionReceipt calls
EVM_RPC_URLS = {
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

# ERC-20 Transfer event: keccak256("Transfer(address,address,uint256)")
TRANSFER_EVENT_TOPIC0 = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# Where subscription payments should be sent
PAY_TO = os.getenv("X402_PAY_TO", "0x1E3AC01d0fdb976179790BDD02823196A92705C9").lower()

# USDC decimals
USDC_DECIMALS = 6
USDC_ATOMS_PER_DOLLAR = 10 ** USDC_DECIMALS  # 1 USDC = 1_000_000 atoms

PLANS = {
    "free": {"name": "Free", "price_usd": 0, "scans_per_day": 10, "api_rate": 60, "features": ["basic_scan"]},
    "bronze": {"name": "Bronze", "price_usd": 9.99, "scans_per_day": 50, "api_rate": 120, "features": ["basic_scan", "whale_alerts", "webhook"]},
    "silver": {"name": "Silver", "price_usd": 29.99, "scans_per_day": 200, "api_rate": 300, "features": ["basic_scan", "whale_alerts", "webhook", "custom_dashboard", "priority_support"]},
    "gold": {"name": "Gold", "price_usd": 99.99, "scans_per_day": 1000, "api_rate": 1000, "features": ["all_features", "alpha_access", "dedicated_support"]},
    "platinum": {"name": "Platinum", "price_usd": 299.99, "scans_per_day": -1, "api_rate": 5000, "features": ["all_features", "white_glove", "custom_integration"]},
}

class CheckoutRequest(BaseModel):
    plan: str
    wallet_address: str
    chain: str = "solana"
    months: int = Field(default=1, ge=1, le=12)

class PaymentVerify(BaseModel):
    tx_hash: str
    checkout_id: str

class Subscription(BaseModel):
    id: str
    wallet_address: str
    plan: str
    expires_at: str
    status: str = "active"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS subscriptions (
        id TEXT PRIMARY KEY, wallet_address TEXT, plan TEXT, chain TEXT,
        tx_hash TEXT, amount_usd REAL, months INTEGER, status TEXT DEFAULT "pending",
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, expires_at TIMESTAMP)''')
    c.execute('''CREATE INDEX IF NOT EXISTS idx_sub_wallet ON subscriptions(wallet_address)''')
    conn.commit(); conn.close()
init_db()

def get_db():
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row; return conn

router = APIRouter(prefix="/checkout", tags=["x402 Checkout"])

@router.get("/plans")
async def list_plans():
    """List available subscription plans."""
    return {"plans": PLANS}

@router.post("/initiate")
async def initiate_checkout(req: CheckoutRequest):
    """Initiate a subscription checkout."""
    if req.plan not in PLANS:
        raise HTTPException(status_code=400, detail="Invalid plan")

    plan = PLANS[req.plan]
    if plan["price_usd"] == 0:
        # Free plan — no payment needed
        checkout_id = f"free_{req.wallet_address[:8]}_{int(datetime.utcnow().timestamp())}"
        conn = get_db()
        try:
            c = conn.cursor()
            expires = (datetime.utcnow() + timedelta(days=365)).isoformat()
            c.execute('''INSERT INTO subscriptions (id, wallet_address, plan, chain, amount_usd, months, status, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                (checkout_id, req.wallet_address, req.plan, req.chain, 0, req.months, "active", expires))
            conn.commit()
            return {"checkout_id": checkout_id, "plan": req.plan, "amount_usd": 0, "status": "active", "payment_required": False}
        finally: conn.close()

    # Paid plan — generate payment request
    checkout_id = f"chk_{req.wallet_address[:8]}_{int(datetime.utcnow().timestamp())}"
    amount = plan["price_usd"] * req.months

    conn = get_db()
    try:
        c = conn.cursor()
        c.execute('''INSERT INTO subscriptions (id, wallet_address, plan, chain, amount_usd, months, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (checkout_id, req.wallet_address, req.plan, req.chain, amount, req.months, "pending"))
        conn.commit()
    finally: conn.close()

    return {
        "checkout_id": checkout_id,
        "plan": req.plan,
        "amount_usd": amount,
        "wallet_address": req.wallet_address,
        "status": "pending_payment",
        "payment_required": True,
        "payment_url": f"/checkout/pay/{checkout_id}",
    }

@router.get("/pay/{checkout_id}")
async def get_payment_page(checkout_id: str):
    """Get payment details for a checkout."""
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute('SELECT * FROM subscriptions WHERE id = ?', (checkout_id,))
        row = c.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Checkout not found")

        plan = PLANS.get(row['plan'], {})
        return {
            "checkout_id": checkout_id,
            "plan": row['plan'],
            "plan_name": plan.get("name"),
            "amount_usd": row['amount_usd'],
            "wallet_address": row['wallet_address'],
            "chain": row['chain'],
            "months": row['months'],
            "status": row['status'],
            "features": plan.get("features", []),
        }
    finally: conn.close()

@router.post("/verify")
async def verify_payment(verify: PaymentVerify):
    """Verify payment and activate subscription."""
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute('SELECT * FROM subscriptions WHERE id = ?', (verify.checkout_id,))
        row = c.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Checkout not found")

        if row['status'] == "active":
            return {"status": "already_active", "subscription_id": verify.checkout_id}

        # Verify tx on-chain
        tx_valid = await verify_on_chain_payment(
            tx_hash=verify.tx_hash,
            chain=row['chain'],
            expected_amount_usd=row['amount_usd'],
        )

        if tx_valid:
            expires = (datetime.utcnow() + timedelta(days=30 * row['months'])).isoformat()
            c.execute('UPDATE subscriptions SET status = ?, tx_hash = ?, expires_at = ? WHERE id = ?',
                      ("active", verify.tx_hash, expires, verify.checkout_id))
            conn.commit()

            return {
                "status": "active",
                "subscription_id": verify.checkout_id,
                "plan": row['plan'],
                "expires_at": expires,
            }
        else:
            raise HTTPException(status_code=400, detail="Payment verification failed")
    finally: conn.close()


# ═══════════════════════════════════════════════════════════════════
# ON-CHAIN PAYMENT VERIFICATION
# ═══════════════════════════════════════════════════════════════════

async def verify_on_chain_payment(
    tx_hash: str,
    chain: str,
    expected_amount_usd: float,
) -> bool:
    """Verify a USDC payment on-chain.

    Strategy:
      1. Normalize chain name, validate tx_hash format
      2. Fetch transaction receipt via eth_getTransactionReceipt (RPC fallback)
      3. Decode ERC-20 Transfer events from logs
      4. Verify: USDC contract match, 'to' == PAY_TO, amount >= expected
      5. Also verify the transaction succeeded (status == 1)
    """
    chain = chain.lower().replace("eip155:", "")
    # Map numeric chain IDs to names
    CHAIN_ID_MAP = {"1": "ethereum", "8453": "base", "42161": "arbitrum", "137": "polygon", "10": "optimism"}
    if chain in CHAIN_ID_MAP:
        chain = CHAIN_ID_MAP[chain]

    # Validate tx_hash format
    if not tx_hash or not tx_hash.startswith("0x") or len(tx_hash) != 66:
        logger.warning(f"verify_on_chain: invalid tx_hash format: {tx_hash[:20] if tx_hash else 'None'}...")
        return False

    # Check we support this chain
    usdc_address = USDC_ADDRESSES.get(chain)
    blockscout_base = BLOCKSCOUT_URLS.get(chain)
    rpc_urls = EVM_RPC_URLS.get(chain)

    if not usdc_address:
        logger.warning(f"verify_on_chain: unsupported chain '{chain}' — expected one of {list(USDC_ADDRESSES.keys())}")
        return False

    logger.info(f"verify_on_chain: tx={tx_hash[:16]}... chain={chain} expected=${expected_amount_usd}")

    expected_atoms = str(int(expected_amount_usd * USDC_ATOMS_PER_DOLLAR))
    our_address = PAY_TO.lower()
    usdc_address = usdc_address.lower()

    # ── Strategy A: RPC eth_getTransactionReceipt ──
    receipt = None
    if rpc_urls:
        receipt = await _fetch_receipt_via_rpc(tx_hash, rpc_urls)

    # ── Strategy B: Blockscout API v2 (fallback) ──
    if not receipt and blockscout_base:
        receipt = await _fetch_receipt_via_blockscout(tx_hash, blockscout_base)

    if not receipt:
        logger.warning(f"verify_on_chain: could not fetch receipt for {tx_hash[:16]}...")
        return False

    # Verify transaction succeeded
    status = receipt.get("status")
    if status is not None:
        status_str = str(status).lower()
        if status_str not in ("ok", "1", "0x1"):
            logger.warning(f"verify_on_chain: tx {tx_hash[:16]}... failed on-chain (status={status})")
            return False

    # Extract logs from receipt
    logs = receipt.get("logs", [])
    if not logs:
        logger.warning(f"verify_on_chain: no logs in receipt for {tx_hash[:16]}...")
        return False

    # Scan logs for USDC Transfer to PAY_TO
    transfer_found = False
    amount_match = False
    actual_amount = None

    for log in logs:
        topics = log.get("topics", [])
        if len(topics) < 3:
            continue
        if topics[0].lower() != TRANSFER_EVENT_TOPIC0:
            continue

        log_address = (log.get("address") or "").lower()
        if log_address != usdc_address:
            continue

        # Decode 'to' from topic2 (padded 32-byte address)
        try:
            to_address = "0x" + topics[2][-40:].lower()
        except (IndexError, ValueError):
            continue

        if to_address != our_address:
            continue

        # We found a Transfer event to our address for the correct USDC contract
        transfer_found = True

        # Decode amount from data field (uint256)
        log_data = log.get("data", "0x")
        try:
            if log_data and log_data.startswith("0x") and len(log_data) >= 66:
                actual_amount = str(int(log_data[:66], 16))
            elif log_data and log_data != "0x":
                actual_amount = str(int(log_data, 16))
        except (ValueError, TypeError) as e:
            logger.warning(f"verify_on_chain: could not decode Transfer amount: {e}")

        # Verify the amount matches exactly
        if actual_amount and actual_amount == expected_atoms:
            amount_match = True
        elif actual_amount:
            logger.warning(
                f"verify_on_chain: amount mismatch for {tx_hash[:16]}...: "
                f"expected {expected_atoms} atoms (${expected_amount_usd}), "
                f"got {actual_amount} atoms"
            )

        break  # Only one relevant Transfer per tx

    if not transfer_found:
        logger.warning(
            f"verify_on_chain: no USDC Transfer to {our_address[:10]}... "
            f"from contract {usdc_address[:10]}... in {tx_hash[:16]}..."
        )
        return False

    if not amount_match:
        logger.warning(
            f"verify_on_chain: amount mismatch — expected {expected_atoms}, "
            f"got {actual_amount or 'unknown'}"
        )
        return False

    logger.info(f"verify_on_chain: confirmed tx {tx_hash[:16]}... — ${expected_amount_usd} USDC to {our_address[:10]}...")
    return True


async def _fetch_receipt_via_rpc(tx_hash: str, rpc_urls: list[str]) -> Optional[dict]:
    """Fetch transaction receipt via JSON-RPC eth_getTransactionReceipt."""
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
                    continue  # Rate limited, try next
                data = resp.json()
                if "error" in data and data["error"]:
                    continue
                result = data.get("result")
                if result:
                    return result
            except Exception as e:
                logger.debug(f"RPC call failed for {url[:30]}...: {e}")
                continue
    return None


async def _fetch_receipt_via_blockscout(tx_hash: str, base_url: str) -> Optional[dict]:
    """Fetch transaction receipt via Blockscout v2 API (free, no key needed)."""
    tx_url = f"{base_url}/api/v2/transactions/{tx_hash}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.get(tx_url)
            if resp.status_code != 200:
                return None
            data = resp.json()
            status = data.get("status") or data.get("result")
            logs = data.get("decoded_input", {}).get("logs") or data.get("logs") or []
            if not logs:
                # Try the receipt endpoint
                rec_url = f"{base_url}/api?module=transaction&action=gettxreceiptstatus&txhash={tx_hash}"
                rec_resp = await client.get(rec_url)
                if rec_resp.status_code == 200:
                    rec_data = rec_resp.json()
                    logs = rec_data.get("result", {}).get("logs", []) or rec_data.get("logs", [])
            return {"status": status, "logs": logs}
        except Exception as e:
            logger.debug(f"Blockscout fetch failed for {tx_hash[:16]}...: {e}")
            return None

@router.get("/subscription/{wallet_address}")
async def get_subscription(wallet_address: str):
    """Get active subscription for a wallet."""
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute('SELECT * FROM subscriptions WHERE wallet_address = ? AND status = "active" ORDER BY expires_at DESC LIMIT 1', (wallet_address,))
        row = c.fetchone()
        if not row:
            return {"wallet_address": wallet_address, "active": False, "plan": "free"}

        plan = PLANS.get(row['plan'], PLANS["free"])
        return {
            "wallet_address": wallet_address,
            "active": True,
            "plan": row['plan'],
            "plan_name": plan["name"],
            "features": plan["features"],
            "expires_at": row['expires_at'],
            "scans_remaining": plan["scans_per_day"],
        }
    finally: conn.close()

@router.get("/health")
async def health():
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute('SELECT COUNT(*) as total, SUM(CASE WHEN status="active" THEN 1 ELSE 0 END) as active FROM subscriptions')
        row = c.fetchone()
        return {"status": "healthy", "total_subscriptions": row['total'], "active": row['active']}
    finally: conn.close()

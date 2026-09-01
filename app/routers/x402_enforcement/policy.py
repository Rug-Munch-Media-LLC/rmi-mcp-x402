"""x402_enforcement.policy — trial usage + refund-policy helpers."""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import redis as _redis_types

from app.routers.x402_enforcement.config import TRIAL_REDIS_PREFIX, TRIAL_WINDOW_SECONDS, get_redis
from app.routers.x402_enforcement.pricing import TOOL_PRICES

logger = logging.getLogger("x402_enforcement")


def check_trial(tool_id: str, client_id: str, max_trials: int | None = None) -> tuple:
    """Check (and optionally consume) a trial credit for this client/tool.

    Returns (can_use: bool, remaining: int).
    If max_trials is None, reads default from TOOL_PRICES (trial_free field).
    """
    if not client_id:
        return False, 0
    if max_trials is None:
        max_trials = TOOL_PRICES.get(tool_id, {}).get("trial_free", 3)
    if not isinstance(max_trials, int) or max_trials < 1:
        # Defensive: pricing table could carry a non-int/null trial_free.
        max_trials = 3
    r = get_redis()
    if not r:
        return True, max_trials  # Redis down → fail open (don't block)
    key = f"{TRIAL_REDIS_PREFIX}{client_id}:{tool_id}"
    try:
        used = int(r.get(key) or 0)
        if used >= max_trials:
            return False, 0
        # Atomically increment (consume one trial)
        pipe = r.pipeline()
        pipe.incr(key)
        pipe.expire(key, TRIAL_WINDOW_SECONDS)
        results = pipe.execute()
        new_count = results[0]
        return True, max(0, max_trials - new_count)
    except Exception:
        return True, max_trials


logger = logging.getLogger("x402_enforcement_v2")


def _record_refundable_payment(
    tx_hash: str, chain: str, payer: str, amount: str, tool: str, reason: str
):
    """Record a refundable payment in Redis when a tool returns empty data.

    Stores in Redis key 'x402:refund:{tx_hash}' with 7-day TTL.
    Actual USDC refund is a manual process — we send USDC back from whichever chain has funds.
    """
    r = get_redis()
    if not r:
        logger.warning(f"Cannot record refund for {tx_hash}: Redis unavailable")
        return

    refund_key = f"x402:refund:{tx_hash}"
    refund_data = {
        "tx_hash": tx_hash,
        "chain": chain or "unknown",
        "payer": payer or "unknown",
        "amount_atoms": str(amount) if amount else "0",
        "tool": tool or "unknown",
        "reason": reason,
        "status": "refundable",  # refundable -> requested -> processing -> completed
        "flagged_at": time.time(),
        "requested_at": None,
        "processed_at": None,
    }
    try:
        r.setex(refund_key, 7 * 86400, json.dumps(refund_data))  # 7-day TTL
        logger.info(f"Recorded refundable payment: {tx_hash[:16]}... tool={tool} reason={reason}")
    except Exception as e:
        logger.error(f"Failed to record refund for {tx_hash}: {e}")


# ── Trial tracking (Redis-based) ──
_redis_client: _redis_types.Redis | None | bool = None  # bool = unavailable sentinel

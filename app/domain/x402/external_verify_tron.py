# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Rug Munch Media LLC
# --- external_verify_tron.py: fail-closed TRC-20 USDT self-verify fallback ---
from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from typing import Any

from .facilitator_config import X402_NETWORK_WALLETS


async def verify_tron_fallback(
    tx_hash: str,
    network_id: str,
    asset: str,
    amount_usd: float,
    enforce_replay: Callable[[str, str], Awaitable[Any]],
) -> dict[str, Any]:
    """TRC-20 USDT self-verify (ported from the Pry production verifier).

    Only USDT on the canonical TRON contract is creditable — fail closed
    on any other TRC-20 token.
    """
    if str(asset).upper() not in ("USDT", "USDT-TRON"):
        return {
            "verified": False,
            "reason": f"asset {asset!r} not TRC-20-verifiable (USDT only)",
            "settlement": None,
            "network": network_id,
        }
    recipient = (X402_NETWORK_WALLETS.get(network_id) or "").strip()
    if not recipient:
        return {
            "verified": False,
            "reason": "TRON receiving wallet not configured — set X402_WALLET_TRON",
            "settlement": None,
            "network": network_id,
        }
    # Late import keeps tron_verify optional (same as pre-split behavior).
    from .tron_verify import (
        DEFAULT_TRON_RPC_URL,
        verify_tron_trc20_payment,
    )

    rpc = os.getenv("X402_TRON_RPC", DEFAULT_TRON_RPC_URL)
    result = await verify_tron_trc20_payment(tx_hash, recipient, amount_usd, rpc_url=rpc)
    if result.get("verified"):
        await enforce_replay(tx_hash, network_id)
    return {
        "verified": bool(result.get("verified")),
        "reason": result.get("reason", ""),
        "settlement": None,
        "network": network_id,
        "facilitator": "tron-grid",
        "signer": result.get("recipient") or recipient,
        "amount_atomic": result.get("amount_raw"),
        "asset": asset,
        "contract": result.get("contract"),
        "confirmed_at": result.get("confirmed_at"),
        "amount_usd": result.get("amount_usd"),
    }

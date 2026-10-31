# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Rug Munch Media LLC
# --- external_verify_solana.py: SPL USDC/USDT self-verify via RPC ---
from __future__ import annotations

import os
from typing import Any

from .facilitator_config import X402_NETWORK_WALLETS
from .pricing import normalize_network_id
from .solana_verifier import (
    DEFAULT_SOLANA_RPC_URL,
    SPL_ASSET_MINTS,
    verify_solana_payment,
)

# Comma-separated fallback Solana RPCs tried in order on transport failures.
SOLANA_RPC_FALLBACKS_ENV = "X402_SOLANA_RPC_FALLBACKS"

__all__ = ["SOLANA_RPC_FALLBACKS_ENV", "verify_solana"]


async def verify_solana(
    payment_id: str,
    tx_hash: str,
    network: str,
    asset: str,
    amount_usd: float,
) -> dict[str, Any]:
    network_id = normalize_network_id(network)
    if not payment_id or not tx_hash:
        return {
            "verified": False,
            "reason": "needs_facilitator: missing payment_id/tx_hash in header",
            "settlement": None,
            "scaffolded": True,
            "network": network_id,
        }
    # SPL-verifiable assets: USDC + USDT (mint table in solana_verifier). A
    # symbol outside the table cannot be attributed to a mint on-chain.
    mint = SPL_ASSET_MINTS.get(str(asset).upper())
    if mint is None:
        return {
            "verified": False,
            "reason": f"asset {asset!r} not SPL-verifiable",
            "settlement": None,
            "network": network_id,
        }
    recipient = (X402_NETWORK_WALLETS.get(network_id) or "").strip()
    if not recipient:
        return {
            "verified": False,
            "reason": "solana receiving wallet not configured — set X402_WALLET_SOLANA",
            "settlement": None,
            "network": network_id,
        }
    rpc = os.getenv("X402_SOLANA_RPC", DEFAULT_SOLANA_RPC_URL)
    fallbacks = [
        part.strip() for part in os.getenv(SOLANA_RPC_FALLBACKS_ENV, "").split(",") if part.strip()
    ]
    result = await verify_solana_payment(
        tx_hash,
        recipient,
        amount_usd,
        rpc_url=rpc,
        fallback_rpc_urls=fallbacks,
        expected_mint=mint,
    )
    return {
        "verified": bool(result.get("verified")),
        "reason": result.get("reason", ""),
        "settlement": None,
        "network": network_id,
        "facilitator": "solana-rpc",
        # ``payer`` is the transferChecked authority (the actual payer); the
        # recipient wallet is only a fallback for legacy responses.
        "signer": result.get("payer") or result.get("recipient") or recipient,
        "amount_atomic": result.get("amount_raw"),
        "asset": asset,
        "mint": result.get("mint"),
        "confirmed_at": result.get("confirmed_at"),
        "amount_usd": result.get("amount_usd"),
    }

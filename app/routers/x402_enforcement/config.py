"""x402_enforcement.config — shared constants, chain configs, verifier state.

Hoists the module-level state that multiple enforcement modules reference so the
102KB monolith can be split without changing runtime behavior. PRESERVE imports via
:mod:`app.routers.x402_enforcement`."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("x402_enforcement")

# Trial tracking
TRIAL_REDIS_PREFIX = "x402:trial:"
TRIAL_WINDOW_SECONDS = 86400  # 24h rolling window

logger2 = logging.getLogger("x402_enforcement_v2")

# Discovery endpoint cache
_discovery_cache: dict | None = None
_discovery_cache_time: float = 0
DISCOVERY_CACHE_TTL = 300  # 5 minutes

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "1; mode=block",
    "Cache-Control": "no-store",
}

# x402 SDK Verifier (lazy init)
VERIFIER = None  # kept for backward compat — always None now
_FACILITATOR_CONFIGS = None
_TOKEN_METADATA = None
_verifier_loaded = False


def _ensure_verifier():
    """Initialize the x402 SDK verifier singleton."""
    global _verifier_loaded
    if _verifier_loaded:
        return True
    _verifier_loaded = True
    try:
        from app.routers.x402_verifier import get_verifier

        get_verifier()  # Initializes on first call
        logger.info("x402 enforcement: SDK verifier initialized")
        return True
    except Exception as e:
        logger.error(f"x402 enforcement: SDK verifier init failed: {e}")
        return False


# Multi-chain USDC configs + pay-to resolution
# ── Multi-chain USDC configs ──
# PAYMENT chains: Base and Solana use facilitators (fast, federated verification).
# All other EVM chains use self-verification via Etherscan/Alchemy on-chain checks.
# Same EVM wallet works across all chains — user pays on whichever has USDC.
# This is a competitive advantage: most x402 gateways only take Base.
CHAIN_USDC = {
    # ── Facilitator-verified chains (instant/direct) ──
    "base": {
        "network": "eip155:8453",
        "chain_id": 8453,
        "usdc": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        "name": "USD Coin",
        "version": "2",
        "method": "local_eip712",
        "verify": "facilitator",
        "facilitators": ["coinbase_cdp", "payai"],
    },
    "solana": {
        "network": "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp",
        "chain_id": None,
        "usdc": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        "name": "USD Coin",
        "version": "2",
        "method": "payai",
        "verify": "facilitator",
        "facilitators": ["payai"],
    },
    "bsc": {
        "network": "eip155:56",
        "chain_id": 56,
        "usdc": "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
        "name": "USD Coin",
        "version": "2",
        "method": "local_eip712",
        "verify": "facilitator",
        "facilitators": ["eip7702"],
    },  # pieverse REMOVED — OFFLINE (api.pieverse.xyz NXDOMAIN)
    "ethereum": {
        "network": "eip155:1",
        "chain_id": 1,
        "usdc": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "name": "USD Coin",
        "version": "2",
        "method": "local_eip712",
        "verify": "facilitator",
        "facilitators": ["primev", "payai", "cloudflare_x402", "eip7702"],
    },
    "tron": {
        "network": "tron:mainnet",
        "chain_id": None,
        "usdc": "TEkxiTehnzSmSe2XqrBj4w32RUN966rdz8",
        "name": "USD Coin (TRC20)",
        "version": "2",
        "method": "tron_selfverify",
        "verify": "facilitator",
        "facilitators": ["tron_selfverify"],
        "tokens": {
            "USDT": "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
            "USDD": "TPYmHEhy5n8TCEfZGqW2rPbmgh1fGqNBPa",
        },
    },
    "bitcoin": {
        "network": "bitcoin:mainnet",
        "chain_id": None,
        "usdc": "",
        "name": "Bitcoin",
        "version": "2",
        "method": "bitcoin_selfverify",
        "verify": "facilitator",
        "facilitators": ["bitcoin_selfverify"],
        "tokens": {"BTC": "native"},
    },
    # ── Self-verified EVM chains (EIP-7702 universal) ──
    "arbitrum": {
        "network": "eip155:42161",
        "chain_id": 42161,
        "usdc": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
        "name": "USD Coin",
        "version": "2",
        "method": "local_eip712",
        "verify": "self",
        "facilitators": ["eip7702"],
    },
    "optimism": {
        "network": "eip155:10",
        "chain_id": 10,
        "usdc": "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85",
        "name": "USD Coin",
        "version": "2",
        "method": "local_eip712",
        "verify": "self",
        "facilitators": ["eip7702"],
    },
    "polygon": {
        "network": "eip155:137",
        "chain_id": 137,
        "usdc": "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",
        "name": "USD Coin",
        "version": "2",
        "method": "local_eip712",
        "verify": "self",
        "facilitators": ["eip7702"],
    },
    "avalanche": {
        "network": "eip155:43114",
        "chain_id": 43114,
        "usdc": "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E",
        "name": "USD Coin",
        "version": "2",
        "method": "local_eip712",
        "verify": "self",
        "facilitators": ["eip7702"],
    },
    "fantom": {
        "network": "eip155:250",
        "chain_id": 250,
        "usdc": "0x04068DA6C83AFCFA0e13ba15A6696662335D5B75",
        "name": "USD Coin",
        "version": "2",
        "method": "local_eip712",
        "verify": "self",
        "facilitators": ["eip7702"],
    },
    "gnosis": {
        "network": "eip155:100",
        "chain_id": 100,
        "usdc": "0xDDAfbb505ad214D7b80b1f830fcCc89B60fb7A83",
        "name": "USD Coin",
        "version": "2",
        "method": "local_eip712",
        "verify": "self",
        "facilitators": ["eip7702"],
    },
    # ── SEPA/EUR (AsterPay fiat off-ramp) ──
    "sepa": {
        "network": "sepa:eur",
        "chain_id": None,
        "usdc": "",
        "name": "Euro",
        "version": "1",
        "method": "asterpay",
        "verify": "facilitator",
        "facilitators": ["asterpay"],
        "tokens": {"EUR": "fiat"},
    },
}

# Pay-to addresses — pulled dynamically from WalletManagerV2
# Fallback: env vars or legacy hardcoded addresses


def _get_pay_to_address(chain: str) -> str:
    """Get active x402 payment address from WalletManagerV2, with fallbacks."""
    try:
        from app.walletpress_manager import get_wallet_manager_v2

        mgr = get_wallet_manager_v2(os.getenv("WALLET_VAULT_PASSWORD", ""))
        for w in mgr._wallets.values():
            if w.chain == chain and w.x402_enabled and w.status == "active":
                return w.address
    except Exception:
        pass
    # Fallback: env vars
    if chain == "eth":
        return os.getenv("X402_EVM_PAY_TO", "0x1E3AC01d0fdb976179790BDD02823196A92705C9")
    if chain == "sol":
        return os.getenv("X402_SOL_PAY_TO", "Gix4P9AmwcZRGzr2hCEME5m2QAvY86dBfm8c7e7MpFzv")
    # Try to find any EVM wallet for EVM chains
    if chain in ("base", "polygon", "arbitrum", "optimism", "avalanche", "bsc", "fantom", "gnosis"):
        return _get_pay_to_address("eth")
    return ""


# Deferred — wallet_manager_v2 pulls bip_utils+monero (~450ms)
EVM_PAY_TO = None
SOL_PAY_TO = None
_pay_addresses_loaded = False


def _ensure_pay_addresses():
    global EVM_PAY_TO, SOL_PAY_TO, _pay_addresses_loaded
    if _pay_addresses_loaded:
        return
    _pay_addresses_loaded = True
    EVM_PAY_TO = _get_pay_to_address("eth")
    SOL_PAY_TO = _get_pay_to_address("sol")


# ── Tool pricing (parsed from gateway configs) ──
TOOL_PRICES: dict[str, dict[str, Any]] = {}


CHAIN_NAMES = {
    "base": "Base",
    "solana": "Solana",
    "ethereum": "Ethereum",
    "bsc": "BNB Chain",
    "tron": "TRON",
    "bitcoin": "Bitcoin",
    "arbitrum": "Arbitrum",
    "optimism": "Optimism",
    "polygon": "Polygon",
    "avalanche": "Avalanche",
    "fantom": "Fantom",
    "gnosis": "Gnosis",
    "sepa": "SEPA (EUR)",
}

# ── x402 v2 spec helpers ──

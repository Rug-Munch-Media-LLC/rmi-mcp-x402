#!/usr/bin/env python3
"""
RugMunch Data MCP — DEX chain/address normalization.

Single source of truth for canonical chain names, the per-provider ids
(DexScreener chainId vs GeckoTerminal network id), and address canonicalization.
Both DEX tool modules import this; no provider id is hardcoded anywhere else.
"""

import re

_EVM_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_SOL_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")

# canonical name -> accepted spellings (case-insensitive)
ALIASES = {
    "ethereum": ("eth", "ethereum", "erc20", "mainnet"),
    "solana": ("sol", "solana"),
    "bsc": ("bsc", "bnb", "binance", "binance-smart-chain"),
    "base": ("base",),
    "arbitrum": ("arb", "arbitrum", "arbitrum-one"),
    "polygon": ("matic", "polygon", "polygon-pos"),
    "optimism": ("op", "optimism"),
    "avalanche": ("avax", "avalanche"),
}

_ALIAS_INDEX = {alias: canon for canon, names in ALIASES.items() for alias in names}

# canonical name -> provider id (only where the provider id differs or is known)
PROVIDER_IDS = {
    "dexscreener": {
        "ethereum": "ethereum",
        "solana": "solana",
        "bsc": "bsc",
        "base": "base",
        "arbitrum": "arbitrum",
        "polygon": "polygon",
        "optimism": "optimism",
        "avalanche": "avalanche",
    },
    "geckoterminal": {
        "ethereum": "eth",
        "solana": "solana",
        "bsc": "bsc",
        "base": "base",
        "arbitrum": "arbitrum",
        "polygon": "polygon_pos",
        "optimism": "optimism",
        "avalanche": "avax",
    },
}


def canonical_chain(chain: str) -> str:
    """Map any accepted spelling to the canonical chain name (lowercase id)."""
    key = (chain or "").strip().lower()
    if not key:
        raise ValueError("chain is required")
    if key in _ALIAS_INDEX:
        return _ALIAS_INDEX[key]
    if _ID_RE.match(key):
        return key  # provider-native id we have no alias for: pass through
    raise ValueError(f"unrecognized chain: {chain!r}")


def provider_chain(chain: str, provider: str) -> str:
    """Canonical chain -> that provider's chain/network id."""
    if provider not in PROVIDER_IDS:
        raise ValueError(f"unknown provider: {provider!r}")
    canon = canonical_chain(chain)
    return PROVIDER_IDS[provider].get(canon, canon)


def normalize_address(chain: str, address: str) -> str:
    """Canonicalize a token/pool address for the chain (EVM lowercased)."""
    addr = (address or "").strip()
    if not addr:
        raise ValueError("address is required")
    if canonical_chain(chain) == "solana":
        if not _SOL_RE.match(addr):
            raise ValueError(f"invalid solana address: {address!r}")
        return addr  # base58 is case-sensitive: never lowercase
    if addr.startswith("0x") or addr.startswith("0X"):
        if not _EVM_RE.match(addr):
            raise ValueError(f"invalid EVM address: {address!r}")
        return addr.lower()
    if _ID_RE.match(addr.lower()):
        return addr  # pair ids on non-EVM DEXes are opaque strings
    raise ValueError(f"invalid address: {address!r}")


def normalize_pair_id(chain: str, pair: str) -> str:
    """DexScreener pair ids follow the token rule (base58 on Solana, 0x on EVM)."""
    return normalize_address(chain, pair)

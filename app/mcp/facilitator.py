"""Multi-chain payment facilitator configuration for the x402 MCP server."""

from __future__ import annotations

import os


class MultiChainFacilitator:
    """Multi-chain payment facilitator."""

    base_config = {
        "name": "RugMunch Intelligence x402 Gateway",
        "network": "BASE",
        # Canonical X402_WALLET; legacy X402_BASE_WALLET as read-time alias.
        # No hardcoded default — unset fails closed to "".
        "wallet_address": os.environ.get("X402_WALLET") or os.environ.get("X402_BASE_WALLET", ""),
        "token_symbol": "ETH",
        "token_decimals": 18,
        "rpc_url": "https://mainnet.base.org",
        "explorer": "https://basescan.org",
    }

    solana_config = {
        "name": "RugMunch Intelligence x402 Gateway",
        "network": "SOLANA",
        # Canonical X402_WALLET_SOLANA; legacy X402_SOLANA_WALLET as alias.
        # No hardcoded default — unset fails closed to "".
        "wallet_address": os.environ.get("X402_WALLET_SOLANA")
        or os.environ.get("X402_SOLANA_WALLET", ""),
        "token_symbol": "USDC",
        "token_decimals": 6,
        "rpc_url": "https://api.mainnet-beta.solana.com",
        "explorer": "https://solscan.io",
    }

    third_party = {
        "base": "https://mcp.rugmunch.io",
        "solana": "https://mcp.rugmunch.io",
    }

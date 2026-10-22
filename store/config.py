"""Runtime configuration for the Rug Munch Intelligence MCP store.

Every value is an env var with a safe default so the store boots on a bare
box and can be pointed at staging without code changes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: Base mainnet USDC — the only asset the store advertises on Base.
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
USDC_BASE_SEPOLIA = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"

#: CAIP-2 ids.
NETWORK_BASE = "eip155:8453"
NETWORK_BASE_SEPOLIA = "eip155:84532"

#: Coffer's settlement wallet (verified live 2026-10-23 from GET /v1/x402/payment).
#: Advertise THIS address or Coffer reconciliation rejects the settlement.
DEFAULT_PAY_TO = "0x66Aa99d25CAD3c9680abc2636812c720329aA690"


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


@dataclass(frozen=True, slots=True)
class Settings:
    """Resolved process configuration."""

    backend_url: str
    coffer_url: str
    pay_to: str
    network: str
    asset_contract: str
    asset_symbol: str
    testnet_network: str
    testnet_asset: str
    public_base_url: str
    db_path: Path
    daily_budget_usd: float
    free_tier_enabled: bool
    timeout_seconds: float
    admin_key: str


def load_settings() -> Settings:
    """Build Settings from the environment (called once at startup)."""
    return Settings(
        backend_url=os.getenv("RMI_BACKEND_URL", "http://127.0.0.1:8000").rstrip("/"),
        coffer_url=os.getenv("COFFER_URL", "http://127.0.0.1:8001").rstrip("/"),
        pay_to=os.getenv("STORE_PAY_TO", DEFAULT_PAY_TO),
        network=os.getenv("STORE_NETWORK", NETWORK_BASE),
        asset_contract=os.getenv("STORE_ASSET", USDC_BASE),
        # Coffer invoices name the asset by SYMBOL ("USDC"); sending the
        # contract address is rejected as "asset not supported".
        asset_symbol=os.getenv("STORE_ASSET_SYMBOL", "USDC"),
        # Sepolia is advertised as a second accepts[] entry so testers can pay
        # without spending mainnet USDC (Coffer settles eip155:84532).
        testnet_network=os.getenv("STORE_TESTNET_NETWORK", NETWORK_BASE_SEPOLIA),
        testnet_asset=os.getenv("STORE_TESTNET_ASSET", USDC_BASE_SEPOLIA),
        public_base_url=os.getenv("STORE_PUBLIC_BASE_URL", "http://127.0.0.1:8026").rstrip("/"),
        db_path=Path(os.getenv("STORE_DB_PATH", "/var/lib/rmi-mcp-store/store.db")),
        daily_budget_usd=_float("STORE_DAILY_BUDGET_USD", 1.0),
        free_tier_enabled=os.getenv("STORE_FREE_TIER", "1") not in {"0", "false", "no"},
        timeout_seconds=_float("STORE_TIMEOUT_SECONDS", 25.0),
        admin_key=os.getenv("STORE_ADMIN_KEY", ""),
    )

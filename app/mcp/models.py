"""Data models for the x402 MCP server."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class X402Bundle:
    """Complete x402 tool bundle definition."""

    bundle_id: str
    name: str
    description: str
    category: str
    endpoints: list[dict[str, Any]] = field(default_factory=list)
    price_usd: float = 0.0
    tier: str = "standard"  # free, trial, standard, premium, enterprise
    is_premium: bool = False
    requires_payment: bool = True

    @property
    def base_eth(self) -> str:
        return f"{self.price_usd / 3000:.6f} ETH"

    @property
    def solana_usdc(self) -> str:
        return f"{self.price_usd:.2f} USDC"

    def dict(self) -> dict[str, Any]:
        return {
            "bundle_id": self.bundle_id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "endpoints": self.endpoints,
            "endpoint_count": len(self.endpoints),
            "price_usd": self.price_usd,
            "tier": self.tier,
            "is_premium": self.is_premium,
            "requires_payment": self.requires_payment,
            "pricing": {"base": self.base_eth, "solana": self.solana_usdc},
        }

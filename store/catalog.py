"""The store catalog — every paid tool, its price, free tier and schema.

Single source of truth: the MCP tool list, `/.well-known/x402` and
`/store/catalog` are all rendered from ``TOOLS``. Nothing else may hardcode a
price.

PRICE FLOOR (measured 2026-10-23): Coffer authorises every verify/settle
against its own ``api_access`` price, currently $0.01, and rejects
under-payment outright. A tool advertised below that floor would take a
client's money and then fail settlement, so ``price_usd`` here is the
*authorised* price and ``intended_usd`` records the marketing intent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Coffer's authoritative floor for an `api_access` verify (USD).
COFFER_PRICE_FLOOR_USD = 0.01


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """One purchasable tool."""

    name: str
    title: str
    description: str
    intended_usd: float
    free_daily: int
    tier: str
    capability: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    example: dict[str, Any] = field(default_factory=dict)

    @property
    def price_usd(self) -> float:
        """Authorised price — never below Coffer's settlement floor."""
        return max(self.intended_usd, COFFER_PRICE_FLOOR_USD)

    @property
    def price_atoms(self) -> int:
        """Price in USDC atoms (1 atom = $0.000001)."""
        return int(round(self.price_usd * 1_000_000))


_ADDRESS_IN = {
    "type": "string",
    "description": "0x-prefixed EVM address",
    "pattern": "^0x[0-9a-fA-F]{40}$",
}

TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="intel.scan",
        title="Token risk scan",
        description=(
            "Risk verdict for a token: safety score, risk level, honeypot flags and "
            "confidence, from the live scanner."
        ),
        intended_usd=0.005,
        free_daily=3,
        tier="basic",
        capability="scanner.token.scan",
        input_schema={
            "type": "object",
            "properties": {"chain": {"type": "string", "default": "base"}, "address": _ADDRESS_IN},
            "required": ["address"],
        },
        output_schema={
            "type": "object",
            "properties": {
                "verdict": {"type": "string"},
                "risk_score": {"type": ["number", "null"]},
                "risk_level": {"type": "string"},
                "flags": {"type": "array"},
            },
        },
        example={"chain": "base", "address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"},
    ),
    ToolSpec(
        name="scanner.token",
        title="Token security snapshot",
        description=(
            "Full token snapshot: DEX pair data, liquidity, price, holders where "
            "available, plus the scanner safety score."
        ),
        intended_usd=0.01,
        free_daily=3,
        tier="basic",
        capability="token.detail",
        input_schema={
            "type": "object",
            "properties": {"chain": {"type": "string", "default": "base"}, "address": _ADDRESS_IN},
            "required": ["address"],
        },
        output_schema={
            "type": "object",
            "properties": {
                "token": {"type": "object"},
                "security": {"type": "object"},
                "liquidity": {"type": "object"},
            },
        },
        example={"chain": "base", "address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"},
    ),
    ToolSpec(
        name="security.wallet",
        title="Wallet security screen",
        description=(
            "Wallet threat screen: curated labels, scammer/good-actor posture and "
            "deployer profile for an address."
        ),
        intended_usd=0.02,
        free_daily=2,
        tier="basic",
        capability="wallet.security",
        input_schema={
            "type": "object",
            "properties": {"chain": {"type": "string", "default": "base"}, "address": _ADDRESS_IN},
            "required": ["address"],
        },
        output_schema={
            "type": "object",
            "properties": {
                "risk_level": {"type": "string"},
                "labels": {"type": "array"},
                "is_scammer": {"type": "boolean"},
            },
        },
        example={"chain": "base", "address": "0x1E3AC01d0fdb976179790BDD02823196A92705C9"},
    ),
    ToolSpec(
        name="news.search",
        title="Crypto news search",
        description=(
            "Search the live crypto news headline feed. Filters real headlines by "
            "keyword; returns the freshest items when no keyword matches."
        ),
        intended_usd=0.01,
        free_daily=50,
        tier="free",
        capability="news.headlines",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keyword(s) to match in headline text"},
                "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 50},
            },
            "required": ["query"],
        },
        output_schema={
            "type": "object",
            "properties": {"matched": {"type": "boolean"}, "articles": {"type": "array"}},
        },
        example={"query": "solana", "limit": 5},
    ),
    ToolSpec(
        name="exploit.lookup",
        title="Exploit & scam lookup",
        description=(
            "Semantic lookup over the scam/exploit intelligence collection: known "
            "attack patterns, incidents and their mechanics."
        ),
        intended_usd=0.02,
        free_daily=2,
        tier="premium",
        capability="rag.search",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Attack pattern, protocol or technique"},
                "limit": {"type": "integer", "default": 5, "minimum": 1, "maximum": 20},
            },
            "required": ["query"],
        },
        output_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}, "hits": {"type": "array"}},
        },
        example={"query": "flash loan oracle manipulation", "limit": 3},
    ),
)

_BY_NAME: dict[str, ToolSpec] = {tool.name: tool for tool in TOOLS}


def get_tool(name: str) -> ToolSpec | None:
    """Resolve a tool by its dotted public name."""
    return _BY_NAME.get(name)

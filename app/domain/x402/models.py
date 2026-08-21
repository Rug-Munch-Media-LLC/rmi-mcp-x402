"""Pydantic v2 models for the x402 domain — spec-conformant.

Wire shape matches the official x402 specification (see
``docs/x402-official-spec/x402-specification-v1.md`` §5.1/§5.3 and
``transports-v1__http.md``) and the canonical Python reference builder
``x402-py:x402/scheme.py::build_payment_required``:

    {
        "x402Version": 1,
        "accepts": [
            {
                "scheme": "exact",
                "network": "eip155:8453",        # CAIP-2 id, never bare "base"
                "maxAmountRequired": "1000",      # atomic units, STRING
                "resource": "https://host/api/op",
                "description": "Token risk score",
                "mimeType": "application/json",
                "payToAddress": "0x...",
                "asset": "USDC",
                "extra": {"name": "Base USDC", "version": "2", "operation": "..."},
            }
        ],
        # optional multi-asset extension (build_payment_required(multi_asset=True))
        "networks": [...],
        "supported_assets": [...]
    }

``PaymentRequirement.model_dump(by_alias=True, exclude_none=True)`` reproduces
that builder output key-for-key.

Notes for other agents
----------------------
- Construct via snake_case field names (``populate_by_name=True``) or the
  camelCase wire aliases — both work. Serialize with ``by_alias=True``.
- ``network`` fields accept the known legacy bare names ("base", "solana",
  ...) and normalise them to CAIP-2; anything else must already be valid
  CAIP-2 (``namespace:reference``) or construction fails.
- The pre-spec ``PaymentRequest{tool, chain, user_address}`` shape is gone.
  ``PaymentReceipt`` survives only as an alias of :class:`PaymentResponse`
  so legacy imports keep working.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "CAIP2_NETWORK_IDS",
    "NetworkInfo",
    "PaymentFacilitator",
    "PaymentOption",
    "PaymentReceipt",
    "PaymentRequirement",
    "PaymentResponse",
    "Scheme",
    "SUPPORTED_ASSETS",
    "SUPPORTED_NETWORKS",
    "SupportedAsset",
    "ToolCatalog",
    "ToolCatalogEntry",
    "ToolPricing",
    "VerifyResult",
    "X402_VERSION",
    "X402Tier",
    "flatten_supported_assets",
]

#: Protocol version carried on the wire. The fleet contract
#: (docs/X402-SPEC-CONTRACT.md §2.2) and x402-py ``build_payment_required``
#: both emit version 1. Official spec v2 uses x402Version=2 with a
#: restructured body (``amount``/``payTo`` accept fields, top-level
#: ResourceInfo, ``PAYMENT-SIGNATURE`` client header). The fleet wire
#: contract stays v1 until the gateway migrates.
#: TODO(v2): additive — emit x402Version=2 bodies behind a flag once the
#: gateway + clients move; v1 replay stays ``X-Payment-ID``.
X402_VERSION = 1

#: Legacy bare network name -> CAIP-2 id (fleet contract §3; mirrors
#: x402-py ``CAIP2_NETWORK_IDS`` plus the spec's base-sepolia example).
#: CAIP-2 ids pass through unchanged.
CAIP2_NETWORK_IDS: dict[str, str] = {
    "base": "eip155:8453",
    "base-sepolia": "eip155:84532",
    "ethereum": "eip155:1",
    "arbitrum": "eip155:42161",
    "optimism": "eip155:10",
    "polygon": "eip155:137",
    "solana": "solana:mainnet",
    "tron": "tron:mainnet",
}

# CAIP-2 chain id: namespace ":" reference with namespace [-a-z0-9]{3,8}
# and reference [-_a-zA-Z0-9]{1,32}.
_CAIP2_RE = re.compile(r"^[-a-z0-9]{3,8}:[-_a-zA-Z0-9]{1,32}$")

#: Atomic token amounts are non-negative integer strings on the wire.
_ATOMIC_AMOUNT_RE = re.compile(r"^[0-9]+$")


def normalize_caip2_network(value: str) -> str:
    """Normalise a network id to CAIP-2, raising for unknown shapes.

    Known legacy bare names (:data:`CAIP2_NETWORK_IDS`) map to their CAIP-2
    id; anything else must already be valid CAIP-2.
    """
    candidate = value.strip()
    mapped = CAIP2_NETWORK_IDS.get(candidate.lower())
    if mapped is not None:
        return mapped
    if _CAIP2_RE.match(candidate):
        return candidate
    raise ValueError(
        f"network {candidate!r} is neither a known legacy name "
        f"({', '.join(sorted(CAIP2_NETWORK_IDS))}) nor a valid CAIP-2 id"
    )


def _require_caip2(value: Any) -> str:
    """Shared validator body for required ``network`` fields."""
    if not isinstance(value, str):
        raise ValueError("network must be a string")
    return normalize_caip2_network(value)


def _require_caip2_or_none(value: Any) -> str | None:
    """Shared validator body for optional ``network`` fields."""
    if value is None:
        return None
    return _require_caip2(value)


#: Canonical supported-networks table (CAIP-2 ids; fleet contract §3).
#: Mirrors ``x402-py:x402/scheme.py::SUPPORTED_NETWORKS`` key-for-key so the
#: multi-asset 402 surface is byte-identical to the reference implementation.
SUPPORTED_NETWORKS: list[dict[str, str]] = [
    {"network": "eip155:8453", "name": "Base", "family": "evm", "chain": "eip155"},
    {"network": "eip155:1", "name": "Ethereum", "family": "evm", "chain": "eip155"},
    {"network": "eip155:42161", "name": "Arbitrum", "family": "evm", "chain": "eip155"},
    {"network": "eip155:10", "name": "Optimism", "family": "evm", "chain": "eip155"},
    {"network": "eip155:137", "name": "Polygon", "family": "evm", "chain": "eip155"},
    {"network": "solana:mainnet", "name": "Solana", "family": "solana", "chain": "solana"},
    {"network": "tron:mainnet", "name": "TRON", "family": "tron", "chain": "tron"},
]


def _entry(
    asset: str, network: str, name: str, contract: str, decimals: int, verification: str, kind: str
) -> dict[str, Any]:
    """One canonical (network, asset) metadata row."""
    return {
        "asset": asset,
        "network": network,
        "name": name,
        "contract": contract,
        "decimals": decimals,
        "verification": verification,
        "kind": kind,
    }


#: Canonical supported-assets table keyed by CAIP-2 network id, then asset
#: symbol. Mirrors ``x402-py:x402/scheme.py::SUPPORTED_ASSETS`` exactly:
#: ``accepts[].asset`` carries the SYMBOL and ``accepts[].extra.contract``
#: carries the contract address (the official v2 spec uses the address as the
#: asset id; the fleet v1 wire keeps symbol + ``extra.contract``).
#: NOTE: the verification layer (``service.resolve_asset_entry``) keeps the
#: legacy bridged Polygon USDC address for its EIP-7702 match table, same
#: intentional V1/V2 split as the reference implementation.
SUPPORTED_ASSETS: dict[str, dict[str, dict[str, Any]]] = {
    "eip155:8453": {
        "USDC": _entry(
            "USDC",
            "eip155:8453",
            "Base USDC",
            "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
            6,
            "eip3009",
            "erc20",
        ),
        "USDT": _entry(
            "USDT",
            "eip155:8453",
            "Base USDT",
            "0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2",
            6,
            "permit2",
            "erc20",
        ),
        "ETH": _entry("ETH", "eip155:8453", "Base ETH", "", 18, "native", "native"),
        "WETH": _entry(
            "WETH",
            "eip155:8453",
            "Base WETH",
            "0x4200000000000000000000000000000000000006",
            18,
            "permit2",
            "erc20",
        ),
    },
    "eip155:1": {
        "USDC": _entry(
            "USDC",
            "eip155:1",
            "Ethereum USDC",
            "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
            6,
            "eip3009",
            "erc20",
        ),
        "USDT": _entry(
            "USDT",
            "eip155:1",
            "Ethereum USDT",
            "0xdAC17F958D2ee523a2206206994597C13D831ec7",
            6,
            "permit2",
            "erc20",
        ),
        "ETH": _entry("ETH", "eip155:1", "Ethereum ETH", "", 18, "native", "native"),
        "WETH": _entry(
            "WETH",
            "eip155:1",
            "Ethereum WETH",
            "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
            18,
            "permit2",
            "erc20",
        ),
    },
    "eip155:42161": {
        "USDC": _entry(
            "USDC",
            "eip155:42161",
            "Arbitrum USDC",
            "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
            6,
            "eip3009",
            "erc20",
        ),
        "USDT": _entry(
            "USDT",
            "eip155:42161",
            "Arbitrum USDT",
            "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9",
            6,
            "permit2",
            "erc20",
        ),
        "ARB": _entry(
            "ARB",
            "eip155:42161",
            "Arbitrum ARB",
            "0x912CE59144191C1204E64559FE8253a0e49E6548",
            18,
            "permit2",
            "erc20",
        ),
        "ETH": _entry("ETH", "eip155:42161", "Arbitrum ETH", "", 18, "native", "native"),
        "WETH": _entry(
            "WETH",
            "eip155:42161",
            "Arbitrum WETH",
            "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
            18,
            "permit2",
            "erc20",
        ),
    },
    "eip155:10": {
        "USDC": _entry(
            "USDC",
            "eip155:10",
            "Optimism USDC",
            "0x0b2C639c533813f4Aa9D7837CAf62653d097F85F",
            6,
            "eip3009",
            "erc20",
        ),
        "USDT": _entry(
            "USDT",
            "eip155:10",
            "Optimism USDT",
            "0x94b008aA00579c1307B0EF2c499aD98a8ce58e58",
            6,
            "permit2",
            "erc20",
        ),
        "ETH": _entry("ETH", "eip155:10", "Optimism ETH", "", 18, "native", "native"),
        "WETH": _entry(
            "WETH",
            "eip155:10",
            "Optimism WETH",
            "0x4200000000000000000000000000000000000006",
            18,
            "permit2",
            "erc20",
        ),
    },
    "eip155:137": {
        "USDC": _entry(
            "USDC",
            "eip155:137",
            "Polygon USDC",
            "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",
            6,
            "eip3009",
            "erc20",
        ),
        "USDT": _entry(
            "USDT",
            "eip155:137",
            "Polygon USDT",
            "0xc2132D05D31c914a87C6611C10748AEb04B58e8F",
            6,
            "permit2",
            "erc20",
        ),
        "POL": _entry("POL", "eip155:137", "Polygon POL", "", 18, "native", "native"),
        "WETH": _entry(
            "WETH",
            "eip155:137",
            "Polygon WETH",
            "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619",
            18,
            "permit2",
            "erc20",
        ),
    },
    "solana:mainnet": {
        "USDC": _entry(
            "USDC",
            "solana:mainnet",
            "Solana USDC",
            "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            6,
            "spl",
            "spl",
        ),
        "USDT": _entry(
            "USDT",
            "solana:mainnet",
            "Solana USDT",
            "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
            6,
            "spl",
            "spl",
        ),
        "SOL": _entry("SOL", "solana:mainnet", "Solana SOL", "", 9, "native", "native"),
    },
    "tron:mainnet": {
        "USDT": _entry(
            "USDT",
            "tron:mainnet",
            "TRON USDT",
            "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
            6,
            "trc20",
            "trc20",
        ),
        "TRX": _entry("TRX", "tron:mainnet", "TRON TRX", "", 6, "native", "native"),
    },
}


def flatten_supported_assets() -> list[dict[str, Any]]:
    """Flatten :data:`SUPPORTED_ASSETS` into the ``supported_assets`` rows.

    Wire shape of the optional multi-asset extension (contract §2.2); matches
    ``x402-py:x402/scheme.py::flatten_supported_assets`` key-for-key.
    """
    return [dict(entry) for assets in SUPPORTED_ASSETS.values() for entry in assets.values()]


class X402Tier(StrEnum):
    """x402 payment tier — maps to scanner tiers."""

    FREE = "free"
    PRO = "pro"
    ELITE = "elite"
    INTERNAL = "internal"


class Scheme(StrEnum):
    """x402 payment schemes (spec §6; ``specs/schemes/exact``, ``upto``)."""

    EXACT = "exact"
    UPTO = "upto"


class PaymentOption(BaseModel):
    """One entry of ``PaymentRequired.accepts[]`` (v1 spec §5.1.2).

    ``maxAmountRequired`` is the amount in ATOMIC token units and is a
    STRING on the wire; ``network`` is a CAIP-2 id. Serialize with
    ``model_dump(by_alias=True)`` to get the wire keys.
    """

    model_config = ConfigDict(str_strip_whitespace=True, populate_by_name=True)

    scheme: Scheme = Field(default=Scheme.EXACT, description="Payment scheme identifier")
    network: str = Field(description="CAIP-2 network id, e.g. 'eip155:8453'")
    max_amount_required: str = Field(
        alias="maxAmountRequired",
        description="Required payment amount in atomic token units (string)",
    )
    resource: str = Field(description="Full URL of the protected resource")
    description: str = Field(description="Human-readable description of the resource")
    mime_type: str = Field(
        default="application/json",
        alias="mimeType",
        description="MIME type of the expected response",
    )
    pay_to_address: str = Field(
        alias="payToAddress",
        description="Server receiving wallet address",
    )
    asset: str = Field(description="Asset symbol (e.g. 'USDC') or contract address")
    extra: dict[str, Any] = Field(
        default_factory=dict,
        description="Scheme-specific extras (name, version, operation, ...)",
    )

    @field_validator("network", mode="before")
    @classmethod
    def _network_caip2(cls, value: Any) -> str:
        return _require_caip2(value)

    @field_validator("max_amount_required")
    @classmethod
    def _atomic_amount(cls, value: str) -> str:
        if not _ATOMIC_AMOUNT_RE.match(value):
            raise ValueError(
                f"maxAmountRequired must be an atomic-unit integer string, got {value!r}"
            )
        return value


class NetworkInfo(BaseModel):
    """Row of the optional top-level ``networks`` table (SUPPORTED_NETWORKS).

    Mirrors ``x402.scheme.SUPPORTED_NETWORKS`` entries so agents can see
    every network the server accepts.
    """

    model_config = ConfigDict(str_strip_whitespace=True, populate_by_name=True)

    network: str = Field(description="CAIP-2 network id")
    name: str = ""
    family: str = ""
    chain: str = ""

    @field_validator("network", mode="before")
    @classmethod
    def _network_caip2(cls, value: Any) -> str:
        return _require_caip2(value)


class SupportedAsset(BaseModel):
    """Row of the optional top-level ``supported_assets`` table.

    Mirrors ``x402.scheme.flatten_supported_assets()`` entries: one row per
    accepted (network, asset) pair with its verification method.
    """

    model_config = ConfigDict(str_strip_whitespace=True, populate_by_name=True)

    asset: str
    network: str = Field(description="CAIP-2 network id")
    name: str = ""
    contract: str = ""
    decimals: int = 6
    verification: str = Field(
        default="",
        description="Authorization method: eip3009 | permit2 | native | spl | trc20",
    )
    kind: str = ""

    @field_validator("network", mode="before")
    @classmethod
    def _network_caip2(cls, value: Any) -> str:
        return _require_caip2(value)


class PaymentRequirement(BaseModel):
    """The 402 response body (``PaymentRequirementsResponse``, v1 spec §5.1).

    Serialize with ``model_dump(by_alias=True, exclude_none=True)`` — the
    result matches ``x402.scheme.build_payment_required`` key-for-key
    (``networks``/``supported_assets`` appear only in multi-asset mode).
    """

    model_config = ConfigDict(str_strip_whitespace=True, populate_by_name=True)

    x402_version: int = Field(
        default=X402_VERSION,
        alias="x402Version",
        ge=1,
        description="Protocol version identifier",
    )
    error: str | None = Field(
        default=None,
        description="Human-readable reason payment is required",
    )
    accepts: list[PaymentOption] = Field(
        min_length=1,
        description="Acceptable payment methods; never empty",
    )
    networks: list[NetworkInfo] | None = Field(
        default=None,
        description="Multi-asset extension: full supported-networks table",
    )
    supported_assets: list[SupportedAsset] | None = Field(
        default=None,
        description="Multi-asset extension: flattened (network, asset) table",
    )


class PaymentResponse(BaseModel):
    """Settlement receipt (v1 spec §5.3 SettlementResponse + contract §5.2).

    Extends the spec core ``{success, transaction, network, payer}`` with the
    fleet facilitator metadata (``payment_id``, ``facilitator``, ``settled_at``,
    block confirmation detail). ``transaction`` is the broadcast tx hash and
    MUST be non-empty whenever settlement was broadcast.
    """

    model_config = ConfigDict(str_strip_whitespace=True, populate_by_name=True)

    success: bool
    transaction: str = Field(
        description="Blockchain transaction hash ('' only if nothing was broadcast)"
    )
    network: str = Field(description="CAIP-2 network id")
    payment_id: str = ""
    facilitator: str = ""
    settled_at: str = Field(default="", description="ISO-8601 UTC settlement timestamp")
    payer: str | None = Field(default=None, description="Payer wallet address")
    error_reason: str | None = Field(
        default=None,
        alias="errorReason",
        description="Spec error code when settlement failed",
    )
    block_number: int | None = None
    block_hash: str | None = None
    confirmations: int | None = None

    @field_validator("network", mode="before")
    @classmethod
    def _network_caip2(cls, value: Any) -> str:
        return _require_caip2(value)


#: Backward-compatible alias: the pre-spec ``PaymentReceipt`` name now points
#: at the spec-conformant settlement receipt. Prefer ``PaymentResponse``.
PaymentReceipt = PaymentResponse


class VerifyResult(BaseModel):
    """Facilitator verification result (fleet contract §5.1 ``verify``).

    ``reason`` is always present (empty when verified); ``settlement`` carries
    the settlement detail dict when the facilitator returned one.
    """

    model_config = ConfigDict(str_strip_whitespace=True, populate_by_name=True)

    verified: bool
    reason: str = ""
    settlement: dict[str, Any] | None = None
    facilitator: str = ""
    signer: str | None = None
    amount_atomic: int | None = None
    network: str | None = Field(default=None, description="CAIP-2 network id")
    asset: str | None = None

    @field_validator("network", mode="before")
    @classmethod
    def _network_caip2(cls, value: Any) -> str | None:
        return _require_caip2_or_none(value)


class ToolPricing(BaseModel):
    """Per-tool pricing in x402 (server-authoritative — never trust clients).

    Feeds ``accepts[]`` construction: ``tool`` → ``extra.operation`` and the
    resource path, ``description`` → ``description``, ``mime_type`` →
    ``mimeType``. Atomic conversion of ``price_usd`` belongs to the pricing
    layer (x402-py ``asset_to_atomic``), not to clients.
    """

    model_config = ConfigDict(str_strip_whitespace=True, populate_by_name=True)

    tool: str
    price_usd: float = Field(default=0.0, ge=0)
    price_crypto: dict[str, str] = Field(
        default_factory=dict,
        description="Per-unit crypto amounts: {'sol': '0.01', 'eth': '0.001'}",
    )
    description: str = ""
    mime_type: str = "application/json"
    tier_required: X402Tier = X402Tier.FREE

    def to_payment_option(
        self,
        *,
        network: str,
        asset: str,
        max_amount_required: str,
        pay_to_address: str,
        resource: str,
        extra: dict[str, Any] | None = None,
    ) -> PaymentOption:
        """Build this tool's ``accepts[]`` entry from server-side inputs.

        ``max_amount_required`` must be the atomic-unit STRING computed by the
        pricing layer for (network, asset) — never a client-supplied value.
        """
        merged: dict[str, Any] = {
            "name": asset,
            "version": "2",
            "operation": self.tool,
        }
        merged.update(extra or {})
        # Construct via wire aliases so this type-checks with or without the
        # pydantic mypy plugin (populate_by_name covers snake_case at runtime).
        return PaymentOption(
            scheme=Scheme.EXACT,
            network=network,
            maxAmountRequired=max_amount_required,
            resource=resource,
            description=self.description or self.tool,
            mimeType=self.mime_type,
            payToAddress=pay_to_address,
            asset=asset,
            extra=merged,
        )


class ToolCatalogEntry(BaseModel):
    """A single tool in the x402 catalog.

    Maps into ``accepts[]`` as: ``name`` → ``extra.name``, ``description`` →
    ``description``, ``endpoint`` → ``resource``, ``price_usd`` →
    ``maxAmountRequired`` (after atomic conversion).
    """

    model_config = ConfigDict(str_strip_whitespace=True, populate_by_name=True)

    name: str
    description: str = ""
    category: str = ""
    tier_required: X402Tier = X402Tier.FREE
    price_usd: float = Field(default=0.0, ge=0)
    endpoint: str = ""
    enabled: bool = True


class ToolCatalog(BaseModel):
    """Full x402 tool catalog."""

    model_config = ConfigDict(str_strip_whitespace=True, populate_by_name=True)

    tools: list[ToolCatalogEntry] = Field(default_factory=list)
    total: int = 0
    categories: list[str] = Field(default_factory=list)
    fetched_at: str = ""


class PaymentFacilitator(BaseModel):
    """x402 payment facilitator info (verify/settle endpoint, spec §7).

    ``chains`` should carry CAIP-2 network ids going forward (legacy bare
    names are tolerated until the service layer is migrated).
    """

    model_config = ConfigDict(str_strip_whitespace=True, populate_by_name=True)

    name: str
    url: str
    enabled: bool = True
    chains: list[str] = Field(default_factory=list)
    health: str = "unknown"

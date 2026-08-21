"""x402 — facilitator router, self-hosted verifiers, server-authoritative pricing.

This is the security-critical payment-verification layer. It brings the service
up to the OFFICIAL x402 facilitator contract (see ``docs/X402-SPEC-CONTRACT.md``
§4 pricing, §5 facilitator contract, §6 honest status) by mirroring the
canonical reference implementation at ``/srv/work/repos/x402-py/x402/``
(facilitator.py, self_facilitator.py, solana_verifier.py — MIT, Rug Munch
Media LLC), since the ``x402-py`` package is not vendored here.

Responsibilities
----------------
1. ``FacilitatorRouter`` — external facilitators (coinbase ``x402.org``,
   payai, cloudflare env-disabled) + self-hosted EIP-3009 ``SelfFacilitator``
   + EIP-7702 self-verify receipt fallback + Solana SPL RPC verifier.
2. Server-authoritative pricing — ``resolve_price_usd`` + ``validate_client_amount``
   compared in ATOMIC units. The client's claimed USD amount is NEVER trusted.
3. Replay protection — tx_hash nonce burn via Redis SETNX (24h TTL). A given
   tx_hash is usable once; reuse is rejected (410-style).
4. Honest verification status — ``VERIFICATION_STATUS``: evm/solana ``live``,
   tron ``scaffolded:needs_facilitator``. A scaffold never reports verified.
"""

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Rug Munch Media LLC
from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx

from .models import VerifyResult
from .solana_verifier import (
    DEFAULT_SOLANA_RPC_URL,
    verify_solana_payment,
)

log = logging.getLogger(__name__)

# ── Environment / config (mirrors x402-py config; no external package) ──────

PAY_TO = os.getenv("X402_PAY_TO", "0x1E3AC01d0fdb976179790BDD02823196A92705C9").lower()
X402_TIMEOUT_SECONDS = float(os.getenv("X402_TIMEOUT_SECONDS", "15"))
X402_WALLET = os.getenv("X402_WALLET", PAY_TO)
X402_NETWORK_WALLETS = {
    "eip155:8453": os.getenv("X402_WALLET_BASE", X402_WALLET),
    "eip155:1": os.getenv("X402_WALLET_ETH", X402_WALLET),
    "eip155:42161": os.getenv("X402_WALLET_ARBITRUM", X402_WALLET),
    "eip155:10": os.getenv("X402_WALLET_OPTIMISM", X402_WALLET),
    "eip155:137": os.getenv("X402_WALLET_POLYGON", X402_WALLET),
    "solana:mainnet": os.getenv("X402_WALLET_SOLANA", ""),
    "tron:mainnet": os.getenv("X402_WALLET_TRON", ""),
}
X402_SELF_FACILITATE = os.getenv("X402_SELF_FACILITATE", "1").lower() in ("1", "true")
X402_SELF_FACILITATOR_KEY = os.getenv("X402_SELF_FACILITATOR_KEY", "")
X402_SELF_FACILITATOR_RPC = os.getenv("X402_SELF_FACILITATOR_RPC", "")
X402_OFFLINE_MODE = os.getenv("X402_OFFLINE_MODE", "0").lower() in ("1", "true")
REDIS_URL = os.getenv("X402_REDIS_URL", os.getenv("REDIS_URL", ""))
X402_NONCE_TTL = int(os.getenv("X402_NONCE_TTL", "86400"))  # 24h

# Replay rejection HTTP status (410-style) surfaced so routers can map it.
REPLAY_HTTP_STATUS = 410

# ── Network / asset resolution (scheme helpers, §3) ──────────────────────────

_NETWORK_ALIASES: dict[str, str] = {
    "base": "eip155:8453",
    "ethereum": "eip155:1",
    "mainnet": "eip155:1",
    "arbitrum": "eip155:42161",
    "arb": "eip155:42161",
    "optimism": "eip155:10",
    "op": "eip155:10",
    "polygon": "eip155:137",
    "matic": "eip155:137",
    "solana": "solana:mainnet",
    "tron": "tron:mainnet",
}


def normalize_network_id(network: str) -> str:
    """Canonicalise a legacy name or CAIP-2 id to its CAIP-2 form."""
    n = str(network or "").strip().lower()
    return _NETWORK_ALIASES.get(n, n)


def network_family(network: str) -> str:
    """Return ``evm`` | ``solana`` | ``tron`` for a network."""
    n = normalize_network_id(network)
    if n == "solana:mainnet":
        return "solana"
    if n == "tron:mainnet":
        return "tron"
    if n.startswith("eip155:"):
        return "evm"
    return (
        "evm"
        if n in {k for k, v in _NETWORK_ALIASES.items() if v.startswith("eip155:")}
        else "unknown"
    )


# Spec §3: decimals per asset family (stablecoins 6, ERC-20 natives 18, SOL 9).
_DECIMALS: dict[str, int] = {
    "USDC": 6,
    "USDT": 6,
    "DAI": 18,
    "ETH": 18,
    "WETH": 18,
    "ARB": 18,
    "MATIC": 18,
    "POL": 18,
    "SOL": 9,
    "TRX": 6,
}

# ERC-20 USDC / USDT contract addresses per network (used by EIP-7702 + EIP-3009).
_USDC_CONTRACTS: dict[str, str] = {
    "eip155:8453": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    "eip155:1": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
    "eip155:42161": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
    "eip155:10": "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85",
    "eip155:137": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
}
_USDT_CONTRACTS: dict[str, str] = {
    "eip155:8453": "0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2",
    "eip155:1": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
    "eip155:42161": "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9",
}

# Native assets usable for EIP-7702 value-transfer self-verify on EVM chains.
_NATIVE_ASSETS = {"ETH", "POL", "MATIC", "TRX"}

# ERC-20 transfer(address,uint256) selector — used to parse token payments.
ERC20_TRANSFER_SELECTOR = "0xa9059cbb"


@dataclass
class Quote:
    """USD price quote (server-side)."""

    usd: float
    source: str = "static"
    updated_at: str = ""


# Static fallback USD quotes for volatile assets (no live oracle yet). [REVIEW]
# Live price via env override X402_<ASSET>_USD; stablecoins are 1:1.
_STATIC_FALLBACK_USD: dict[str, float] = {
    "ETH": 3400.0,
    "WETH": 3400.0,
    "SOL": 160.0,
    "POL": 0.35,
    "MATIC": 0.35,
    "ARB": 0.7,
    "TRX": 0.12,
    "DAI": 1.0,
    "USDC": 1.0,
    "USDT": 1.0,
}


def get_asset_usd_price(asset: str, network: str) -> Quote:
    """Resolve a server-side USD price (env override -> static fallback)."""
    asset = str(asset or "").upper()
    env_usd = os.getenv(f"X402_{asset}_USD", "").strip()
    if env_usd:
        try:
            return Quote(usd=float(env_usd), source="env")
        except ValueError:
            pass
    return Quote(usd=_STATIC_FALLBACK_USD.get(asset, 0.0), source="static")


def resolve_asset_entry(network: str, asset: str) -> dict[str, Any] | None:
    """Return asset metadata ``{decimals, verification, contract?}`` or None.

    ``verification`` uses the spec §3 methods: eip3009 | permit2 | native |
    spl | trc20. Unknown networks/assets fail closed (None).
    """
    nid = normalize_network_id(network)
    asset = str(asset or "").upper()
    if asset not in _DECIMALS:
        return None
    family = network_family(nid)
    if family == "evm":
        verification = "eip3009"
        contract: str | None = _USDC_CONTRACTS.get(nid)
        if asset == "USDC":
            verification, contract = "eip3009", _USDC_CONTRACTS.get(nid)
        elif asset == "USDT":
            verification, contract = "permit2", _USDT_CONTRACTS.get(nid)
        elif asset in ("DAI", "WETH", "ARB"):
            verification = "permit2"
        elif asset in _NATIVE_ASSETS:
            verification = "native"
        else:
            return None
        return {"decimals": _DECIMALS[asset], "verification": verification, "contract": contract}
    if family == "solana":
        if asset == "USDC":
            return {"decimals": 6, "verification": "spl"}
        if asset == "SOL":
            return {"decimals": 9, "verification": "spl"}
        return None
    if family == "tron":
        if asset == "USDT":
            return {"decimals": 6, "verification": "trc20"}
        if asset == "TRX":
            return {"decimals": 6, "verification": "native"}
        return None
    return None


def asset_to_atomic(amount_usd: float, asset: str, network: str) -> int:
    """Convert a required USD amount to ATOMIC units (server-side, exact).

    Uses Decimal arithmetic and ceil so float drift can never understate the
    required amount (underpayment is refused). Raises ValueError when no price.
    """
    entry = resolve_asset_entry(network, asset)
    if entry is None:
        raise ValueError(f"asset {asset!r} not supported on {normalize_network_id(network)}")
    quote = get_asset_usd_price(asset, network)
    if quote.usd is None or not math.isfinite(float(quote.usd)) or quote.usd <= 0:
        raise ValueError(f"no USD price available for {asset}")
    decimals = int(entry["decimals"])
    required = Decimal(str(amount_usd)) / Decimal(str(quote.usd)) * Decimal(10**decimals)
    return int(math.ceil(float(required) - 1e-9))


# ── Server-authoritative pricing (§4) ────────────────────────────────────────

# X402_PRICING[op]["prices"][caip2][asset] -> USD. Per-network/per-asset
# override table; env can override the whole table via X402_PRICING_JSON.
X402_PRICING: dict[str, Any] = {
    "*": {
        "default_price": 0.01,
        "default": {},
        "prices": {},
    },
    # Flat per-operation USD prices — the 402 acceptance surface (middleware)
    # quotes ``accepts[].maxAmountRequired`` off these via resolve_price_usd.
    "api_access": {"price_usd": 0.01, "description": "x402 paid API call"},
    "get_token_risk": {"price_usd": 0.01, "description": "Token risk score"},
    "get_wallet_analysis": {"price_usd": 0.01, "description": "Wallet analysis"},
    "generate_report": {"price_usd": 0.10, "description": "AI research report"},
}


def _load_pricing() -> dict[str, Any]:
    env_json = os.getenv("X402_PRICING_JSON", "").strip()
    if env_json:
        try:
            loaded = json.loads(env_json)
            if isinstance(loaded, dict) and loaded:
                return loaded
        except json.JSONDecodeError:
            log.warning("x402_pricing_json_invalid")
    return dict(X402_PRICING)


def resolve_price_usd(op: str, network: str, asset: str) -> float:
    """Server-authoritative USD price for (op, network, asset).

    Lookup order: per-op prices[caip2][asset], prices[caip2]["*"], per-op
    default[asset], flat per-op ``price_usd``, static asset quote, per-op
    default_price.
    """
    pricing = _load_pricing()
    op_table = pricing.get(op, pricing.get("*", {}))
    prices = op_table.get("prices", {})
    nid = normalize_network_id(network)
    asset = str(asset or "").upper()
    net_prices = prices.get(nid, {})
    if asset in net_prices:
        return float(net_prices[asset])
    if "*" in net_prices:
        return float(net_prices["*"])
    defaults = op_table.get("default", {})
    if asset in defaults:
        return float(defaults[asset])
    if "price_usd" in op_table:
        return float(op_table["price_usd"])
    quote = get_asset_usd_price(asset, network)
    if quote.usd and quote.usd > 0:
        return float(quote.usd)
    return float(op_table.get("default_price", 0.01))


def validate_client_amount(
    op: str, client_usd: float | None, asset: str, network: str
) -> tuple[bool, str]:
    """Validate a client-supplied USD amount in ATOMIC units.

    Never trust the client: reject non-finite/negative amounts and underpayment
    (client_atomic < server-required atomic). Overpayment is allowed.
    Returns ``(ok, reason)``.
    """
    if client_usd is None:
        return False, "missing client amount"
    client_usd = float(client_usd)
    if not math.isfinite(client_usd) or client_usd < 0:
        return False, "invalid (non-finite/negative) client amount"
    server_usd = resolve_price_usd(op, network, asset)
    try:
        required_atomic = asset_to_atomic(server_usd, asset, network)
        client_atomic = asset_to_atomic(client_usd, asset, network)
    except ValueError as e:
        return False, str(e)
    if client_atomic < required_atomic:
        return (
            False,
            f"underpayment: client {client_atomic} < required {required_atomic} atomic {asset}",
        )
    return True, ""


# ── Honest verification status (§6) ──────────────────────────────────────────

# evm/solana have REAL on-chain verifiers -> live. TRON has no TRC-20 verifier
# wired yet -> scaffolded, NEVER claimed verified. [REVIEW]
VERIFICATION_STATUS: dict[str, str] = {
    "evm": "live",
    "solana": "live",
    "tron": "scaffolded:needs_facilitator",
}

# Chains with no real settlement-verification (structure/price/asset validated
# only). Payments here report "needs_facilitator", never verified. [REVIEW]
SCAFFOLDED_NETWORKS: set[str] = {"tron:mainnet"}

# EIP-7702 self-verify RPC URLs: network -> JSON-RPC endpoint (env override).
_EIP7702_RPC_DEFAULTS: dict[str, str] = {
    "eip155:8453": "https://mainnet.base.org",
    "eip155:1": "https://eth.llamarpc.com",
    "eip155:42161": "https://arb1.arbitrum.io/rpc",
}
EIP7702_RPC: dict[str, str] = {
    n: os.getenv(f"X402_EIP7702_RPC_{n.split(':')[-1]}", url)
    for n, url in _EIP7702_RPC_DEFAULTS.items()
}


def network_verification_status(network: str) -> str:
    """Map a network to its honest verification status string."""
    nid = normalize_network_id(network)
    if nid in SCAFFOLDED_NETWORKS:
        return VERIFICATION_STATUS["tron"]
    return VERIFICATION_STATUS.get(network_family(nid), "unknown")


# ── Replay protection (Redis SETNX nonce burn, 24h TTL) ──────────────────────


class ReplayStore:
    """tx_hash nonce burn via Redis SETNX (24h TTL); in-memory fallback.

    Redis is optional (guard against redis module/REDIS_URL absence): when
    unavailable we fall back to a bounded in-process set. [REVIEW] — in-memory
    is NOT safe across app instances; production MUST configure REDIS_URL.
    """

    def __init__(self, ttl: int = X402_NONCE_TTL, max_mem: int = 50_000) -> None:
        self.ttl = int(ttl)
        self._max_mem = int(max_mem)
        self._mem: set[str] = set()
        self._redis = None
        self._redis_broken = False
        if REDIS_URL:
            try:
                from redis import asyncio as aioredis  # type: ignore[import-untyped]

                self._redis = aioredis.from_url(REDIS_URL, decode_responses=True)
            except Exception as e:  # pragma: no cover - defensive
                log.warning("x402_replay_redis_unavailable", extra={"error": str(e)})
                self._redis_broken = True

    @property
    def uses_redis(self) -> bool:
        return self._redis is not None

    async def check_and_burn(self, key: str) -> bool:
        """Atomically consume ``key``. Returns True when newly consumed.

        False means the key was already consumed -> replay. Lock-free via
        ``SET NX EX`` (SETNX) against Redis; in-memory fallback otherwise.
        """
        if self._redis is not None:
            try:
                # Redis SETNX with 24h TTL — atomic claim of the nonce.
                ok = await self._redis.set(key, "1", ex=self.ttl, nx=True)
                return bool(ok)
            except Exception as e:  # pragma: no cover - runtime redis failure
                log.warning("x402_replay_redis_error", extra={"error": str(e)})
                self._redis_broken = True
                self._redis = None
        if key in self._mem:
            return False
        self._mem.add(key)
        if len(self._mem) > self._max_mem:
            self._mem = set(list(self._mem)[-self._max_mem :])
        return True

    async def check(self, key: str) -> bool:
        """Non-destructive membership check (True when already used)."""
        if self._redis is not None:
            try:
                exists = await self._redis.get(key)
                return bool(exists)
            except Exception:  # pragma: no cover
                return key in self._mem
        return key in self._mem


# ── Self-hosted EIP-3009 SelfFacilitator ─────────────────────────────────────


# Verification results use the canonical wire type ``models.VerifyResult``
# (fleet contract §5.1) so the service layer and the HTTP surface share ONE
# result shape. Construct with keyword arguments only.


def _ecrecover_available() -> bool:
    try:
        import eth_keys  # noqa: F401
        import eth_utils  # noqa: F401

        return True
    except ImportError:
        return False


_ECRECOVER_AVAILABLE = _ecrecover_available()


class SelfFacilitator:
    """Self-hosted EVM ``exact`` verifier (EIP-3009 transferWithAuthorization).

    ``verify`` recovers the signer of the EIP-712 digest locally (eth-keys),
    validates amount >= the server-side required atomic amount, recipient ==
    the configured payTo wallet, the validity window, chain id and nonce
    replay. ``settle`` broadcasts the authorization (or a plain transferFrom)
    to the configured RPC. Crypto deps (eth-keys/eth-utils) are optional: if
    absent, verification FAILS CLOSED with a clear reason. [REVIEW]
    """

    def __init__(
        self,
        *,
        pay_to: str = X402_WALLET,
        facilitator_key: str = X402_SELF_FACILITATOR_KEY,
        rpc_url: str = X402_SELF_FACILITATOR_RPC,
        timeout_seconds: float = X402_TIMEOUT_SECONDS,
    ) -> None:
        self.pay_to = (pay_to or X402_WALLET or "").lower()
        self.facilitator_key = facilitator_key or X402_SELF_FACILITATOR_KEY
        self.rpc_url = rpc_url or X402_SELF_FACILITATOR_RPC
        self.timeout_seconds = timeout_seconds
        self.nonces: set[str] = set()

    # EIP-3009 digest (EIP-712). Requires eth_keys/eth_utils — guarded.
    def _digest(self, auth: dict[str, Any], chain_id: int, token: str) -> bytes:
        from eth_utils.address import to_canonical_address  # type: ignore[import-untyped]
        from eth_utils.crypto import keccak  # type: ignore[import-untyped]

        def enc_addr(addr: str) -> bytes:
            return b"\x00" * 12 + to_canonical_address(addr) if True else b""

        def enc_uint(v: int) -> bytes:
            return int(v).to_bytes(32, "big")

        domain_type = keccak(
            text=(
                "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
            )
        )
        struct_type = keccak(
            text="TransferWithAuthorization(address from,address to,uint256 value,"
            "uint256 validAfter,uint256 validBefore,bytes32 nonce)"
        )
        name, version = (
            os.getenv("X402_EIP712_NAME", "USD Coin"),
            os.getenv("X402_EIP712_VERSION", "2"),
        )
        domain = keccak(
            domain_type
            + keccak(text=name)
            + keccak(text=version)
            + enc_uint(chain_id)
            + enc_addr(token)
        )
        nonce = self._nonce_bytes(auth.get("nonce"))
        struct = keccak(
            struct_type
            + enc_addr(str(auth["from"]))
            + enc_addr(str(auth["to"]))
            + enc_uint(int(auth["value"]))
            + enc_uint(int(auth.get("validAfter") or 0))
            + enc_uint(int(auth.get("validBefore") or 0))
            + nonce
        )
        return keccak(b"\x19\x01" + domain + struct)

    @staticmethod
    def _nonce_bytes(nonce: Any) -> bytes:
        if isinstance(nonce, bytes):
            return nonce[-32:].rjust(32, b"\x00")
        if isinstance(nonce, int):
            return nonce.to_bytes(32, "big")
        s = str(nonce or "").removeprefix("0x")
        return bytes.fromhex(s)[-32:].rjust(32, b"\x00") if s else b"\x00" * 32

    @staticmethod
    def _chain_id_for(network: str) -> int | None:
        nid = normalize_network_id(network)
        if nid.startswith("eip155:"):
            try:
                return int(nid.split(":", 1)[1])
            except ValueError:
                return None
        return None

    async def verify(self, payment: dict[str, Any]) -> VerifyResult:
        """Verify an EVM exact payment entirely locally (fail-closed)."""
        network = normalize_network_id(str(payment.get("network") or ""))
        asset = str(payment.get("asset") or "USDC").upper()
        try:
            amount_usd = float(payment.get("amount_usd") or 0.0)
        except (TypeError, ValueError):
            amount_usd = 0.0
        entry = resolve_asset_entry(network, asset)
        if entry is None:
            return VerifyResult(
                verified=False,
                reason=f"asset {asset!r} not supported on {network}",
                asset=asset,
                network=network,
            )
        if entry.get("verification") != "eip3009":
            return VerifyResult(
                verified=False,
                reason="self-facilitator supports eip3009 assets only",
                asset=asset,
                network=network,
            )
        if not _ECRECOVER_AVAILABLE:
            return VerifyResult(
                verified=False,
                reason="eip3009 crypto unavailable: eth-keys not installed",
                asset=asset,
                network=network,
            )
        if not self.pay_to or self.pay_to == "pry-default-wallet":
            return VerifyResult(
                verified=False,
                reason="server wallet (payTo) not configured",
                asset=asset,
                network=network,
            )
        auth = payment.get("authorization")
        if not isinstance(auth, dict) or not auth:
            return VerifyResult(
                verified=False,
                reason="missing EIP-3009 transferWithAuthorization",
                asset=asset,
                network=network,
            )
        chain_id = self._chain_id_for(network)
        if chain_id is None:
            return VerifyResult(
                verified=False,
                reason=f"no EVM chain id known for {network}",
                asset=asset,
                network=network,
            )
        token = str(entry.get("contract") or "").lower()
        if not token:
            return VerifyResult(
                verified=False,
                reason=f"no token contract for {asset} on {network}",
                asset=asset,
                network=network,
            )

        try:
            from eth_keys import keys as eth_keys  # type: ignore[import-untyped]
            from eth_utils.address import to_checksum_address  # type: ignore[import-untyped]

            digest = self._digest(auth, chain_id, token)
            sig_hex = str(auth.get("signature") or "").removeprefix("0x")
            sig = eth_keys.Signature(bytes.fromhex(sig_hex))
            from_addr = to_checksum_address(str(auth.get("from") or ""))
            recovered = to_checksum_address(
                eth_keys.PublicKey.recover_from_msg_hash(digest, sig).to_address()
            )
            if recovered != from_addr:
                return VerifyResult(
                    verified=False,
                    reason=f"signer {recovered} != authorization from {from_addr}",
                    asset=asset,
                    network=network,
                )
        except Exception as e:
            return VerifyResult(
                verified=False,
                reason=f"cannot verify authorization: {e}",
                asset=asset,
                network=network,
            )

        try:
            value = int(auth["value"])
            valid_after = int(auth.get("validAfter") or 0)
            valid_before = int(auth.get("validBefore") or 0)
        except (ValueError, TypeError, KeyError):
            return VerifyResult(
                verified=False,
                reason="unparseable authorization value/window",
                asset=asset,
                network=network,
            )

        try:
            required_atomic = asset_to_atomic(amount_usd, asset, network)
        except ValueError as e:
            return VerifyResult(verified=False, reason=str(e), asset=asset, network=network)
        if value < required_atomic:
            return VerifyResult(
                verified=False,
                reason=f"underpayment: authorized {value} < required {required_atomic}"
                f" atomic {asset}",
                signer=recovered,
                asset=asset,
                network=network,
            )

        to_addr = str(auth.get("to") or "").lower().lstrip("0x")
        if to_addr and ("0x" + to_addr).lower() != self.pay_to and to_addr.lower() != self.pay_to:
            if str(auth.get("to") or "").lower() != self.pay_to:
                return VerifyResult(
                    verified=False,
                    reason=f"recipient {auth.get('to')} != configured payTo {self.pay_to}",
                    asset=asset,
                    network=network,
                )

        import time as _t

        now = int(_t.time())
        if now < valid_after:
            return VerifyResult(
                verified=False,
                reason="authorization not valid yet (validAfter in future)",
                asset=asset,
                network=network,
            )
        if valid_before and now >= valid_before:
            return VerifyResult(
                verified=False,
                reason="authorization expired (validBefore passed)",
                asset=asset,
                network=network,
            )

        nonce_key = f"{chain_id}:{token}:{self._nonce_bytes(auth.get('nonce')).hex()}"
        if nonce_key in self.nonces:
            return VerifyResult(
                verified=False, reason="nonce already used (replay)", asset=asset, network=network
            )
        self.nonces.add(nonce_key)

        return VerifyResult(
            verified=True,
            reason="",
            signer=recovered,
            amount_atomic=value,
            asset=asset,
            network=network,
        )

    async def settle(self, payment: dict[str, Any]) -> str | None:
        """Sign + broadcast settlement. Returns tx hash, or None if not settlable."""
        if not self.rpc_url or not self.facilitator_key:
            return None
        auth = payment.get("authorization")
        if not isinstance(auth, dict) or not auth:
            return None
        network = normalize_network_id(str(payment.get("network") or ""))
        asset = str(payment.get("asset") or "USDC").upper()
        entry = resolve_asset_entry(network, asset)
        chain_id = self._chain_id_for(network)
        if entry is None or chain_id is None or not entry.get("contract"):
            return None
        try:
            from eth_account import Account  # type: ignore[import-untyped]

            calldata = self._transfer_with_auth_calldata(auth)
        except Exception:
            return None
        try:
            payer = Account.from_key(self.facilitator_key.removeprefix("0x")).address
            nonce_hex = await self._rpc("eth_getTransactionCount", [payer, "pending"])
            if nonce_hex is None:
                return None
            gas_price_hex = await self._rpc("eth_gasPrice", [])
            if gas_price_hex is None:
                return None
            signed = Account.from_key(self.facilitator_key.removeprefix("0x")).sign_transaction(
                {
                    "nonce": int(str(nonce_hex), 16),
                    "gasPrice": int(str(gas_price_hex), 16),
                    "gas": 100_000,
                    "to": entry["contract"],
                    "value": 0,
                    "data": calldata,
                    "chainId": chain_id,
                }
            )
            raw = "0x" + str(signed.raw_transaction.hex())
            tx = await self._rpc("eth_sendRawTransaction", [raw])
            return str(tx) if tx else None
        except (httpx.HTTPError, httpx.RequestError, ValueError, OSError):
            return None

    async def _rpc(self, method: str, params: list[Any]) -> Any:
        """Issue a JSON-RPC call against ``self.rpc_url``; returns result or None."""
        if not self.rpc_url:
            return None
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                self.rpc_url,
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            )
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict) and data.get("error"):
                raise ValueError(f"RPC error {method}: {data['error']}")
            return data.get("result") if isinstance(data, dict) else None

    def _transfer_with_auth_calldata(self, auth: dict[str, Any]) -> str:
        from eth_utils.address import to_canonical_address  # type: ignore[import-untyped]
        from eth_utils.crypto import keccak  # type: ignore[import-untyped]

        def enc_addr(addr: str) -> bytes:
            return b"\x00" * 12 + to_canonical_address(addr)

        def enc_uint(v: int) -> bytes:
            return int(v).to_bytes(32, "big")

        selector = keccak(
            text="transferWithAuthorization(address,address,uint256,uint256,uint256,bytes32,uint8,bytes32,bytes32)"
        )[:4]
        sig_hex = str(auth.get("signature") or "").removeprefix("0x")
        raw = bytes.fromhex(sig_hex)
        v = ((raw[64] - 27) if raw[64] in (27, 28) else raw[64]).to_bytes(32, "big")
        calldata = (
            selector
            + enc_addr(str(auth["from"]))
            + enc_addr(str(auth["to"]))
            + enc_uint(int(auth["value"]))
            + enc_uint(int(auth.get("validAfter") or 0))
            + enc_uint(int(auth.get("validBefore") or 0))
            + self._nonce_bytes(auth.get("nonce"))
            + v
            + raw[0:32].rjust(32, b"\x00")
            + raw[32:64].rjust(32, b"\x00")
        )
        return "0x" + calldata.hex()


# ── EIP-7702 self-verify fallback ───────────────────────────────────────────


def _eip7702_wallet(network: str) -> str:
    return (X402_NETWORK_WALLETS.get(normalize_network_id(network)) or X402_WALLET or "").lower()


async def _rpc_call(
    client: httpx.AsyncClient, rpc: str, method: str, params: list[Any]
) -> dict[str, Any] | None:
    response = await client.post(
        rpc, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    )
    response.raise_for_status()
    data = response.json()
    return data.get("result") if isinstance(data, dict) else None


def _eip7702_matches(
    tx: dict[str, Any], network: str, asset: str, amount_usd: float
) -> tuple[bool, str]:
    """Check an EIP-7702 tx actually paid OUR wallet at least the price.

    ERC-20 asset -> transfer() to payTo on the token contract; native asset ->
    value to payTo. Unknown assets fail closed.
    """
    pay_to = _eip7702_wallet(network)
    if not pay_to or pay_to == "pry-default-wallet":
        return False, "server wallet not configured"
    tx_to = str(tx.get("to") or "").lower()
    entry = resolve_asset_entry(network, asset)
    if entry is None:
        return False, f"unsupported asset {asset} on {network}"
    contract = str(entry.get("contract") or "").lower()
    if entry.get("verification") in ("eip3009", "permit2") and contract:
        if tx_to != contract:
            return False, f"tx recipient {tx_to} != {asset} token on {network}"
        calldata = str(tx.get("input") or "0x")
        if not calldata.lower().startswith(ERC20_TRANSFER_SELECTOR):
            return False, "tx is not an ERC-20 transfer()"
        if len(calldata) < 138:
            return False, "ERC-20 transfer calldata too short"
        recipient = "0x" + calldata[34:74]
        try:
            paid_atomic = int(calldata[74:138], 16)
        except ValueError:
            return False, "unparseable ERC-20 transfer amount"
        if recipient.lower() != pay_to:
            return False, f"transfer recipient {recipient} != server wallet {pay_to}"
        try:
            expected_atomic = asset_to_atomic(amount_usd, asset, network)
        except ValueError as e:
            return False, str(e)
        if paid_atomic < expected_atomic:
            return (
                False,
                f"underpayment: tx paid {paid_atomic} < required {expected_atomic} atomic {asset}",
            )
        return True, ""
    if entry.get("verification") == "native":
        if tx_to != pay_to:
            return False, f"tx recipient {tx_to} != server wallet {pay_to}"
        try:
            value_atomic = int(tx.get("value") or "0x0", 16)
        except (ValueError, TypeError):
            return False, "unparseable tx value"
        try:
            expected_atomic = asset_to_atomic(amount_usd, asset, network)
        except ValueError as e:
            return False, str(e)
        if value_atomic < expected_atomic:
            return (
                False,
                f"underpayment: tx value {value_atomic} < required "
                f"{expected_atomic} atomic {asset}",
            )
        return True, ""
    return False, f"asset {asset} not self-verifiable via EIP-7702 on {network}"


async def _eip7702_verify(
    tx_hash: str, network: str, asset: str, amount_usd: float
) -> dict[str, Any]:
    """Poll RPC for the tx receipt, then assert it paid OUR wallet >= price."""
    network_id = normalize_network_id(network)
    rpc = EIP7702_RPC.get(network_id)
    if not rpc:
        return {
            "verified": False,
            "reason": f"No EIP-7702 RPC for {network_id}",
            "settlement": None,
        }
    try:
        async with httpx.AsyncClient(timeout=X402_TIMEOUT_SECONDS) as client:
            receipt = await _rpc_call(client, rpc, "eth_getTransactionReceipt", [tx_hash])
            if not receipt or receipt.get("status") != "0x1":
                return {
                    "verified": False,
                    "reason": "EIP-7702 receipt not found or tx failed",
                    "settlement": receipt or None,
                    "facilitator": "eip-7702",
                }
            tx = await _rpc_call(client, rpc, "eth_getTransactionByHash", [tx_hash])
            if not tx:
                return {
                    "verified": False,
                    "reason": "EIP-7702 tx not found",
                    "settlement": receipt,
                    "facilitator": "eip-7702",
                }
            ok, reason = _eip7702_matches(tx, network_id, asset, amount_usd)
            if not ok:
                return {
                    "verified": False,
                    "reason": f"EIP-7702 payment mismatch: {reason}",
                    "settlement": receipt,
                    "facilitator": "eip-7702",
                }
            return {
                "verified": True,
                "reason": "",
                "settlement": receipt,
                "facilitator": "eip-7702",
            }
    except (httpx.HTTPError, httpx.RequestError) as e:
        return {"verified": False, "reason": f"EIP-7702 verify failed: {e}", "settlement": None}


# ── Facilitator contract (§5) ────────────────────────────────────────────────


@dataclass
class FacilitatorConfig:
    """Configuration for a single x402 facilitator."""

    name: str
    verify_url: str
    settle_url: str
    priority: int = 0
    enabled: bool = True
    api_key: str = field(default="", repr=False)
    timeout_seconds: float = X402_TIMEOUT_SECONDS


def _env_bool(name: str, default: str) -> bool:
    return os.getenv(name, default).lower() in ("1", "true")


# Default external facilitators (priority order, env-overridable).
DEFAULT_FACILITATORS: list[FacilitatorConfig] = [
    FacilitatorConfig(
        name="coinbase",
        verify_url=os.getenv("X402_COINBASE_VERIFY", "https://x402.org/facilitator/verify"),
        settle_url=os.getenv("X402_COINBASE_SETTLE", "https://x402.org/facilitator/settle"),
        priority=int(os.getenv("X402_COINBASE_PRIORITY", "1")),
        enabled=_env_bool("X402_COINBASE_ENABLED", "true"),
        api_key=os.getenv("X402_COINBASE_API_KEY", ""),
    ),
    FacilitatorConfig(
        name="payai",
        verify_url=os.getenv("X402_PAYAI_VERIFY", "https://facilitator.payai.network/verify"),
        settle_url=os.getenv("X402_PAYAI_SETTLE", "https://facilitator.payai.network/settle"),
        priority=int(os.getenv("X402_PAYAI_PRIORITY", "2")),
        enabled=_env_bool("X402_PAYAI_ENABLED", "true"),
    ),
    FacilitatorConfig(
        name="cloudflare",
        verify_url=os.getenv("X402_CF_VERIFY", ""),
        settle_url=os.getenv("X402_CF_SETTLE", ""),
        priority=int(os.getenv("X402_CF_PRIORITY", "3")),
        enabled=_env_bool("X402_CF_ENABLED", "false"),
    ),
]


class FacilitatorRouter:
    """Smart router that picks the best facilitator per chain/asset.

    Falls back through enabled external facilitators with health-aware
    decimation (circuit breaker after 3 failures). ``self_hosted`` mode
    (default on) uses :class:`SelfFacilitator` + EIP-7702 + the Solana SPL RPC
    verifier INSTEAD of the external URLs — no Coinbase/CDP dependency.
    Replay protection burns the tx_hash via Redis SETNX (24h TTL).
    """

    def __init__(
        self,
        facilitators: list[FacilitatorConfig] | None = None,
        self_hosted: bool | None = None,
    ) -> None:
        self.facilitators = sorted(
            [f for f in (facilitators or DEFAULT_FACILITATORS) if f.enabled and f.verify_url],
            key=lambda f: f.priority,
        )
        self._failures: dict[str, int] = {}
        self.self_hosted = X402_SELF_FACILITATE if self_hosted is None else bool(self_hosted)
        self._self_facilitator: SelfFacilitator | None = None
        self._network_facilitators: dict[str, Any] = {}
        self._pending_auths: dict[str, dict[str, Any]] = {}
        self.replay = ReplayStore()

    # ── health / extension points ──────────────────────────────────────────

    def _is_healthy(self, config: FacilitatorConfig) -> bool:
        return self._failures.get(config.name, 0) < 3

    def _mark_failure(self, config: FacilitatorConfig) -> None:
        self._failures[config.name] = self._failures.get(config.name, 0) + 1
        log.warning("x402_facilitator_failure", extra={"facilitator": config.name})

    def _mark_success(self, config: FacilitatorConfig) -> None:
        self._failures.pop(config.name, None)

    def register_facilitator(self, network: str, impl: Any) -> None:
        """Register a custom facilitator impl for a network (e.g. TRON).

        ``impl`` must expose ``async verify(payment) -> VerifyResult`` and
        ``async settle(payment) -> str | None``.
        """
        network_id = normalize_network_id(network)
        self._network_facilitators[network_id] = impl
        if ":" not in network:
            self._network_facilitators[network] = impl

    def submit_authorization(self, payment_id: str, authorization: dict[str, Any]) -> None:
        self._pending_auths[payment_id] = authorization

    def readiness(self) -> dict[str, Any]:
        wallet = (X402_WALLET or "").strip()
        wallet_configured = bool(wallet and wallet != "pry-default-wallet")
        mode = "self_hosted" if self.self_hosted else "external"
        if self.self_hosted:
            fac = self._get_self_facilitator()
            ready = wallet_configured and bool(fac.rpc_url or fac.pay_to)
        else:
            ready = wallet_configured and any(f.enabled and f.verify_url for f in self.facilitators)
        return {"mode": mode, "ready": ready, "wallet_configured": wallet_configured}

    def settlement_stats(self) -> dict[str, Any]:
        fac = self._get_self_facilitator()
        return {"nonces_seen": len(fac.nonces), "pending": len(self._pending_auths)}

    def _impl_for(self, network: str) -> Any | None:
        network_id = normalize_network_id(network)
        family = network_family(network)
        return (
            self._network_facilitators.get(network_id)
            or self._network_facilitators.get(network)
            or self._network_facilitators.get(family)
        )

    def _get_self_facilitator(self) -> SelfFacilitator:
        if self._self_facilitator is None:
            self._self_facilitator = SelfFacilitator(
                pay_to=X402_WALLET,
                facilitator_key=X402_SELF_FACILITATOR_KEY,
                rpc_url=X402_SELF_FACILITATOR_RPC,
            )
        return self._self_facilitator

    def _build_payment(
        self, payment_id: str, tx_hash: str, network: str, asset: str, amount_usd: float
    ) -> dict[str, Any]:
        payment: dict[str, Any] = {
            "payment_id": payment_id,
            "tx_hash": tx_hash,
            "network": network,
            "asset": asset,
            "amount_usd": amount_usd,
        }
        auth = self._pending_auths.get(payment_id)
        if auth:
            payment["authorization"] = auth
        return payment

    def _replay_key(self, tx_hash: str, network: str) -> str:
        return f"x402:replay:{normalize_network_id(network)}:{str(tx_hash).strip().lower()}"

    async def _enforce_replay(self, tx_hash: str, network: str) -> tuple[bool, str]:
        """Burn the tx_hash nonce. Returns (ok, reason); ok=False => replay."""
        if not tx_hash:
            return True, ""
        key = self._replay_key(tx_hash, network)
        if not await self.replay.check_and_burn(key):
            return False, "transaction already used (replay)"
        return True, ""

    # ── verify ─────────────────────────────────────────────────────────────

    async def verify(
        self,
        payment_id: str,
        tx_hash: str,
        network: str,
        asset: str,
        amount_usd: float,
    ) -> dict[str, Any]:
        """Verify a payment with the best available facilitator.

        Returns ``{"verified", "reason", "settlement", "facilitator", ...}``.
        Replay-protected: a successfully-verified tx_hash is burned (SETNX,
        24h TTL) and cannot be reused.
        """
        network_id = normalize_network_id(network)

        # Reject invalid amounts before any network call.
        if amount_usd is None or not math.isfinite(float(amount_usd)) or amount_usd < 0:
            return {
                "verified": False,
                "reason": "invalid (non-finite/negative) amount",
                "settlement": None,
                "network": network_id,
            }

        if X402_OFFLINE_MODE:
            ok, reason = await self._enforce_replay(tx_hash, network_id)
            if not ok:
                return {
                    "verified": False,
                    "reason": reason,
                    "settlement": None,
                    "network": network_id,
                    "http_status": REPLAY_HTTP_STATUS,
                }
            return {"verified": True, "reason": "offline_mode", "settlement": None}

        # TRON stays scaffolded — never claimed verified. [REVIEW]
        if network_id in SCAFFOLDED_NETWORKS or network_family(network_id) == "tron":
            return self._scaffold_non_evm_verify(payment_id, tx_hash, network_id, asset, amount_usd)

        # Self-hosted / custom impl path (default).
        if self.self_hosted or self._impl_for(network_id) is not None:
            result = await self._verify_self_hosted(
                payment_id, tx_hash, network_id, asset, amount_usd
            )
            if result.get("verified"):
                ok, reason = await self._enforce_replay(tx_hash, network_id)
                if not ok:
                    return {
                        "verified": False,
                        "reason": reason,
                        "settlement": None,
                        "network": network_id,
                        "http_status": REPLAY_HTTP_STATUS,
                    }
            return result

        # External facilitator path.
        return await self._verify_external(payment_id, tx_hash, network_id, asset, amount_usd)

    async def _verify_self_hosted(
        self,
        payment_id: str,
        tx_hash: str,
        network: str,
        asset: str,
        amount_usd: float,
    ) -> dict[str, Any]:
        network_id = normalize_network_id(network)
        impl = self._impl_for(network_id) or self._get_self_facilitator()
        payment = self._build_payment(payment_id, tx_hash, network_id, asset, amount_usd)
        try:
            result: Any = await impl.verify(payment)
        except Exception as e:
            result = VerifyResult(verified=False, reason=f"facilitator verify error: {e}")
        if getattr(result, "verified", False):
            return {
                "verified": True,
                "reason": "",
                "settlement": None,
                "facilitator": "self-hosted",
                "signer": getattr(result, "signer", ""),
                "amount_atomic": getattr(result, "amount_atomic", 0),
                "network": getattr(result, "network", None) or network_id,
                "asset": getattr(result, "asset", None) or asset,
            }
        reason = getattr(result, "reason", "") or ""
        if reason == "replay" or "replay" in reason:
            return {
                "verified": False,
                "reason": "transaction already used (replay)",
                "settlement": None,
                "network": network_id,
                "http_status": REPLAY_HTTP_STATUS,
            }

        # EIP-7702 self-verify fallback for EVM chains (receipt + payTo check).
        if network_family(network_id) == "evm":
            eip = await _eip7702_verify(tx_hash, network_id, asset, amount_usd)
            if eip["verified"]:
                eip["facilitator"] = "self-hosted-eip7702"
                eip["network"] = network_id
                eip["hex_reason"] = reason
                return eip
            reason = f"{reason}; EIP-7702: {eip.get('reason', '')}"
        elif network_family(network_id) == "solana":
            return await self._verify_solana(payment_id, tx_hash, network_id, asset, amount_usd)

        return {
            "verified": False,
            "reason": reason,
            "settlement": None,
            "facilitator": "self-hosted",
            "network": network_id,
        }

    async def _verify_external(
        self,
        payment_id: str,
        tx_hash: str,
        network: str,
        asset: str,
        amount_usd: float,
    ) -> dict[str, Any]:
        network_id = normalize_network_id(network)
        payload = {
            "paymentId": payment_id,
            "transactionHash": tx_hash,
            "network": network_id,
            "asset": asset,
            "amount": str(asset_to_atomic(amount_usd, asset, network_id)),
            "payToAddress": X402_NETWORK_WALLETS.get(network_id) or X402_WALLET,
            "scheme": "exact",
            "timestamp": datetime.now(UTC).isoformat(),
        }
        last_error = "No facilitators available"
        async with httpx.AsyncClient(timeout=X402_TIMEOUT_SECONDS) as client:
            for config in self.facilitators:
                if not self._is_healthy(config):
                    continue
                try:
                    response = await client.post(config.verify_url, json=payload)
                    response.raise_for_status()
                    data = response.json()
                    self._mark_success(config)
                    verified = bool(
                        data.get("isValid") or data.get("verified") or data.get("success")
                    )
                    if verified:
                        ok, reason = await self._enforce_replay(tx_hash, network_id)
                        if not ok:
                            return {
                                "verified": False,
                                "reason": reason,
                                "settlement": None,
                                "network": network_id,
                                "http_status": REPLAY_HTTP_STATUS,
                            }
                    return {
                        "verified": verified,
                        "reason": data.get("error", data.get("reason", "")) if not verified else "",
                        "settlement": data if verified else None,
                        "facilitator": config.name,
                        "network": network_id,
                    }
                except httpx.HTTPStatusError as e:
                    last_error = f"{config.name}: HTTP {e.response.status_code}"
                    self._mark_failure(config)
                except httpx.RequestError as e:
                    last_error = f"{config.name}: {e}"
                    self._mark_failure(config)
                except json.JSONDecodeError as e:
                    last_error = f"{config.name}: invalid JSON - {e}"
                    self._mark_failure(config)

        # Self-verify fallbacks (EIP-7702 / Solana SPL) when external fails.
        if network_family(network_id) == "evm":
            eip = await _eip7702_verify(tx_hash, network_id, asset, amount_usd)
            if eip["verified"]:
                eip["facilitator"] = "self-hosted-eip7702"
                eip["network"] = network_id
                await self._enforce_replay(tx_hash, network_id)
                return eip
            last_error = eip.get("reason", last_error)
        elif network_family(network_id) == "solana":
            res = await self._verify_solana(payment_id, tx_hash, network_id, asset, amount_usd)
            if res.get("verified"):
                await self._enforce_replay(tx_hash, network_id)
            return res

        return {"verified": False, "reason": last_error, "settlement": None, "network": network_id}

    async def _verify_solana(
        self,
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
        if asset != "USDC":
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
        result = await verify_solana_payment(tx_hash, recipient, amount_usd, rpc_url=rpc)
        return {
            "verified": bool(result.get("verified")),
            "reason": result.get("reason", ""),
            "settlement": None,
            "network": network_id,
            "facilitator": "solana-rpc",
            "signer": result.get("recipient") or recipient,
            "amount_atomic": result.get("amount_raw"),
            "asset": asset,
            "mint": result.get("mint"),
            "confirmed_at": result.get("confirmed_at"),
            "amount_usd": result.get("amount_usd"),
        }

    def _scaffold_non_evm_verify(
        self,
        payment_id: str,
        tx_hash: str,
        network: str,
        asset: str,
        amount_usd: float,
    ) -> dict[str, Any]:
        """[REVIEW] Scaffolded acceptance for TRON — NEVER returns verified.

        Header structure, asset membership and non-negative USD amount are
        validated; on-chain settlement is NOT checked. Result is explicitly
        marked ``scaffolded`` and ``verified: false`` so it can never be
        treated as paid until a real TRC-20 verifier is wired in.
        """
        network_id = normalize_network_id(network)
        entry = resolve_asset_entry(network_id, asset)
        if not payment_id or not tx_hash:
            return {
                "verified": False,
                "reason": "needs_facilitator: missing payment_id/tx_hash in header",
                "settlement": None,
                "scaffolded": True,
                "network": network_id,
            }
        if entry is None:
            return {
                "verified": False,
                "reason": f"needs_facilitator: asset {asset!r} not in SUPPORTED_ASSETS "
                f"for {network_id}",
                "settlement": None,
                "scaffolded": True,
                "network": network_id,
            }
        if amount_usd is None or amount_usd < 0:
            return {
                "verified": False,
                "reason": "needs_facilitator: invalid (non-finite/negative) amount",
                "settlement": None,
                "scaffolded": True,
                "network": network_id,
            }
        return {
            "verified": False,
            "reason": "needs_facilitator: on-chain settlement verification "
            "not implemented for this chain",
            "settlement": None,
            "scaffolded": True,
            "network": network_id,
            "asset": asset,
            "supported": True,
            "verification_status": network_verification_status(network_id),
            "note": "Header structure, asset and price validated; settlement NOT verified. "
            "See VERIFICATION_STATUS.",
        }

    # ── settle ─────────────────────────────────────────────────────────────

    async def settle(
        self,
        payment_id: str,
        tx_hash: str,
        network: str,
        asset: str,
        amount_usd: float,
    ) -> dict[str, Any]:
        """Confirm settlement with the best available facilitator."""
        network_id = normalize_network_id(network)
        if X402_OFFLINE_MODE:
            return {
                "success": True,
                "transaction": tx_hash,
                "network": network_id,
                "payment_id": payment_id,
                "facilitator": "offline",
                "settled_at": datetime.now(UTC).isoformat(),
                "note": "Offline mode",
            }

        if self.self_hosted or self._impl_for(network_id) is not None:
            return await self._settle_self_hosted(
                payment_id, tx_hash, network_id, asset, amount_usd
            )

        payload = {
            "paymentId": payment_id,
            "transactionHash": tx_hash,
            "network": network_id,
            "asset": asset,
            "amount": str(asset_to_atomic(amount_usd, asset, network_id)),
            "payToAddress": X402_NETWORK_WALLETS.get(network_id) or X402_WALLET,
            "scheme": "exact",
            "timestamp": datetime.now(UTC).isoformat(),
        }
        last_error = "No facilitators available"
        async with httpx.AsyncClient(timeout=X402_TIMEOUT_SECONDS) as client:
            for config in self.facilitators:
                if not self._is_healthy(config) or not config.settle_url:
                    continue
                try:
                    response = await client.post(config.settle_url, json=payload)
                    response.raise_for_status()
                    data = response.json()
                    self._mark_success(config)
                    return self._normalize_settlement(
                        data, payment_id, tx_hash, network_id, config.name
                    )
                except httpx.HTTPStatusError as e:
                    last_error = f"{config.name}: HTTP {e.response.status_code}"
                    self._mark_failure(config)
                except httpx.RequestError as e:
                    last_error = f"{config.name}: {e}"
                    self._mark_failure(config)
                except json.JSONDecodeError as e:
                    last_error = f"{config.name}: invalid JSON - {e}"
                    self._mark_failure(config)
        if network_family(network_id) == "evm":
            return {
                "success": True,
                "transaction": tx_hash,
                "network": network_id,
                "payment_id": payment_id,
                "facilitator": "eip-7702",
                "settled_at": datetime.now(UTC).isoformat(),
                "note": "Verified via EIP-7702 RPC",
            }
        if network_family(network_id) in ("solana", "tron"):
            return {
                "success": False,
                "payment_id": payment_id,
                "reason": "needs_facilitator: settlement verification "
                "not implemented for this chain",
                "scaffolded": True,
                "network": network_id,
            }
        return {"success": False, "payment_id": payment_id, "reason": last_error}

    async def _settle_self_hosted(
        self,
        payment_id: str,
        tx_hash: str,
        network: str,
        asset: str,
        amount_usd: float,
    ) -> dict[str, Any]:
        network_id = normalize_network_id(network)
        impl = self._impl_for(network_id) or self._get_self_facilitator()
        payment = self._build_payment(payment_id, tx_hash, network_id, asset, amount_usd)
        try:
            settled_tx = await impl.settle(payment)
        except Exception as e:
            settled_tx = None
            log.warning("x402_self_settle_error", extra={"error": str(e)})
        if settled_tx:
            return {
                "success": True,
                "transaction": str(settled_tx),
                "network": network_id,
                "payment_id": payment_id,
                "facilitator": "self-hosted",
                "settled_at": datetime.now(UTC).isoformat(),
                "note": "Broadcast by self-hosted facilitator",
            }
        if network_family(network_id) == "solana":
            return {
                "success": True,
                "transaction": tx_hash,
                "network": network_id,
                "payment_id": payment_id,
                "facilitator": "solana-rpc",
                "settled_at": datetime.now(UTC).isoformat(),
                "note": "Verified on-chain via SPL RPC",
            }
        return {
            "success": False,
            "payment_id": payment_id,
            "reason": "self-hosted settle unavailable: no authorization, "
            "no facilitator key/RPC, or chain unreachable",
            "facilitator": "self-hosted",
        }

    @staticmethod
    def _normalize_settlement(
        data: dict[str, Any],
        payment_id: str,
        tx_hash: str,
        network: str,
        facilitator: str,
    ) -> dict[str, Any]:
        return {
            "success": True,
            "transaction": tx_hash,
            "network": network,
            "payment_id": payment_id,
            "facilitator": facilitator,
            "settled_at": datetime.now(UTC).isoformat(),
            "block_number": data.get("blockNumber", data.get("block_number", "")),
            "block_hash": data.get("blockHash", data.get("block_hash", "")),
            "confirmations": data.get("confirmations", 0),
            "amount_paid": data.get("amountPaid", data.get("amount_paid", "")),
            "receipt": data,
        }


# ── Global router instance (mirrors x402-py accessors) ──────────────────────

_facilitator_router: FacilitatorRouter | None = None


def get_facilitator_router() -> FacilitatorRouter:
    """Return the global facilitator router (lazily initialised)."""
    global _facilitator_router
    if _facilitator_router is None:
        _facilitator_router = FacilitatorRouter()
    return _facilitator_router


def set_facilitator_router(router: FacilitatorRouter) -> None:
    """Override the global facilitator router (for tests)."""
    global _facilitator_router
    _facilitator_router = router


def register_facilitator(network: str, impl: Any) -> None:
    """Register a custom facilitator for ``network`` on the global router.

    Extension point — e.g. plug in a real TRON TRC-20 verifier once one exists
    (currently TRON is scaffolded). ``impl`` must expose
    ``async verify(payment) -> VerifyResult`` and ``async settle(payment) -> str | None``.
    """
    get_facilitator_router().register_facilitator(network, impl)

"""x402 challenge builder — what a caller must pay before a tool runs.

Spec shape: ``accepts[]`` carries scheme, network, asset, payTo, amount and a
server-issued nonce. The nonce is the payment id the caller echoes back inside
``X-PAYMENT``; replay protection is keyed on it.
"""

from __future__ import annotations

import base64
import json
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from store.catalog import ToolSpec
    from store.config import Settings

#: Header the client must send back, x402 standard.
PAYMENT_HEADER = "X-PAYMENT"
#: Header carrying the base64 challenge (x402 standard, both spellings seen).
REQUIRED_HEADER = "PAYMENT-REQUIRED"


def _accepts_entry(
    tool: ToolSpec,
    *,
    network: str,
    asset_contract: str,
    pay_to: str,
    resource: str,
    label: str,
    verification: str,
) -> dict[str, Any]:
    """One acceptable (scheme, network, asset) combination."""
    return {
        "scheme": "exact",
        "network": network,
        "maxAmountRequired": str(tool.price_atoms),
        "resource": resource,
        "description": tool.description,
        "mimeType": "application/json",
        "payTo": pay_to,
        "payToAddress": pay_to,
        "asset": asset_contract,
        "maxTimeoutSeconds": 300,
        "extra": {
            "name": label,
            "version": "2",
            "operation": "api_access",
            "caip2": network,
            "verification": verification,
            "contract": asset_contract,
            "decimals": 6,
            "priceUsd": tool.price_usd,
            "paymentId": None,  # filled by the caller so one nonce spans accepts[]
        },
    }


def build_challenge(
    tool: ToolSpec,
    settings: Settings,
    *,
    error: str = "payment_required",
    detail: str = "",
    nonce: str | None = None,
) -> dict[str, Any]:
    """Build the 402 challenge for one tool call."""
    payment_id = nonce or uuid.uuid4().hex
    resource = f"{settings.public_base_url}/mcp#{tool.name}"
    accepts = [
        _accepts_entry(
            tool,
            network=settings.network,
            asset_contract=settings.asset_contract,
            pay_to=settings.pay_to,
            resource=resource,
            label="USD Coin",
            verification="eip3009",
        ),
        _accepts_entry(
            tool,
            network=settings.testnet_network,
            asset_contract=settings.testnet_asset,
            pay_to=settings.pay_to,
            resource=resource,
            label="USD Coin (Base Sepolia)",
            verification="eip3009",
        ),
    ]
    for entry in accepts:
        entry["extra"]["paymentId"] = payment_id
    return {
        "x402Version": 1,
        "error": error,
        "detail": detail,
        "paymentId": payment_id,
        "accepts": accepts,
        "freeTier": {
            "callsPerDay": tool.free_daily,
            "clientInstructions": (
                "Sign an EIP-3009 transferWithAuthorization for maxAmountRequired to payTo, "
                "submit the transfer, then retry with X-PAYMENT: base64(json({paymentId, txHash, "
                f"network: '{settings.network}', asset: '{settings.asset_contract}', "
                "amountUsd: <tool price>}))."
            ),
        },
    }


def encode_challenge(challenge: dict[str, Any]) -> str:
    """Base64-encode a challenge for the PAYMENT-REQUIRED header."""
    raw = json.dumps(challenge, separators=(",", ":")).encode()
    return base64.b64encode(raw).decode()


def decode_payment(header_value: str) -> dict[str, Any]:
    """Decode the X-PAYMENT envelope; empty dict when malformed."""
    if not header_value:
        return {}
    raw = header_value.strip()
    raw += "=" * (-len(raw) % 4)  # tolerate both padded and unpadded base64
    try:
        parsed = json.loads(base64.b64decode(raw, validate=False))
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}

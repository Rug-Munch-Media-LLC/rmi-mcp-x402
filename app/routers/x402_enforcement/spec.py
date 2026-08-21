"""x402_enforcement.spec — x402 v2 accepts-list + 402 response builders."""

from __future__ import annotations

import base64 as _base64
import json
import logging
import os
from typing import Any

from fastapi.responses import JSONResponse

from app.routers.x402_enforcement.config import (
    CHAIN_USDC,
    CHAIN_NAMES,
    EVM_PAY_TO,
    SECURITY_HEADERS,
    SOL_PAY_TO,
    _ensure_pay_addresses,
)
from app.routers.x402_enforcement.policy import check_trial
from app.routers.x402_enforcement.pricing import TOOL_PRICES

logger = logging.getLogger("x402_enforcement_v2")


def _build_accepts_list(tool_id: str, pricing: dict) -> list:
    """Build x402 v2 spec 'accepts' array from TOOL_PRICES and CHAIN_USDC.
    _ensure_pay_addresses()

    This produces the canonical PaymentRequirements format that official
    x402 SDKs (@x402/fetch, x402 Python, x402 MCP) parse via PAYMENT-REQUIRED
    header or response body.
    """
    accepts = []
    for chain_key, cfg in CHAIN_USDC.items():
        method = cfg["method"]

        # Determine pay-to address based on chain
        if method == "payai":
            pay_to = SOL_PAY_TO
        elif method == "bitcoin_selfverify":
            pay_to = os.getenv("X402_BTC_PAY_TO", "")
        elif method == "tron_selfverify":
            pay_to = os.getenv("X402_TRON_PAY_TO", "")
        elif method == "asterpay":
            pay_to = os.getenv("ASTERPAY_SEPA_IBAN", "")
        else:
            pay_to = EVM_PAY_TO

        # Determine asset (primary token for the chain)
        asset = cfg.get("usdc", "")
        if not asset and "tokens" in cfg:
            first_token = next(iter(cfg["tokens"].values()), "")
            asset = first_token if first_token != "native" else ""

        extra = {
            "name": cfg["name"],
            "version": cfg["version"],
            "tool": tool_id,
            "chain": chain_key,
        }

        if method == "local_eip712" and cfg.get("chain_id"):
            extra["domain"] = {
                "name": cfg["name"],
                "version": cfg["version"],
                "chainId": cfg["chain_id"],
                "verifyingContract": pay_to,
            }
        elif method == "payai":
            extra["feePayer"] = "2wKupLR9q6wXYppw8Gr2NvWxKBUqm4PPJKkQfoxHDBg4"
        elif method == "tron_selfverify":
            extra["tronNetwork"] = "mainnet"
            extra["trc20Tokens"] = cfg.get("tokens", {})
        elif method == "bitcoin_selfverify":
            extra["paymentNetwork"] = "bitcoin"
            extra["settlementChains"] = ["base", "solana"]
        elif method == "asterpay":
            extra["currency"] = "EUR"
            extra["sepa"] = True

        requirement = {
            "scheme": "exact",
            "network": cfg["network"],
            "asset": asset,
            "amount": pricing["price_atoms"],
            "payTo": pay_to,
            "maxTimeoutSeconds": 180,
            "extra": extra,
        }

        # Add supported tokens for multi-token chains
        if "tokens" in cfg:
            requirement["supportedTokens"] = list(cfg["tokens"].keys())

        accepts.append(requirement)

    return accepts


def _build_bazaar_extension(tool_id: str, pricing: dict, accepts: list) -> dict:
    """Build x402 v2 bazaar extension for facilitator discovery indexing.

    Per x402 v2 bazaar extension spec, the extension follows the standard v2
    pattern: {info: {...}, schema: {...}}.

    - info: Contains the actual discovery data (HTTP method, parameters, output format)
    - schema: JSON Schema (Draft 2020-12) that validates the structure of info

    Per the bazaar spec:
    - input.type: always "http"
    - input.method: HTTP method (GET for tool_id ending in _info/_list, POST for others)
    - output.type: always "json"
    """
    # Map our categories to bazaar-standard categories
    CATEGORY_MAP = {
        "security": "security",
        "intelligence": "intelligence",
        "market": "market-data",
        "analysis": "analytics",
        "social": "social",
        "launchpad": "launchpad",
        "bundle": "bundles",
        "defi": "defi",
        "premium": "premium",
        "api": "api",
        "variant": "crypto",
    }
    category = pricing.get("category", "analysis")
    bazaar_category = CATEGORY_MAP.get(category, category)

    # Determine HTTP method: GET for read-only queries, POST for tools that execute
    read_only_suffixes = ("_info", "_list", "_check", "_scan", "_status", "_health")
    is_read_only = tool_id.endswith(read_only_suffixes)
    http_method = "GET" if is_read_only else "POST"

    # Build input/output from the first accepted payment method
    first_accept = accepts[0] if accepts else {}
    first_accept.get("extra", {})

    # Build inputSchema matching the bazaar spec's info structure
    info_input: dict[str, Any] = {
        "type": "http",
        "method": http_method,
    }

    if http_method == "GET":
        # Query params for GET: address and optional chain
        info_input["queryParams"] = {
            "address": {
                "type": "string",
                "description": f"Blockchain address or identifier for {tool_id}",
            },
            "chain": {"type": "string", "description": "Blockchain to query (default: base)"},
        }
    else:
        # Body params for POST: address and chain
        info_input["bodyType"] = "json"
        info_input["body"] = {
            "address": {
                "type": "string",
                "description": f"Blockchain address or identifier for {tool_id}",
            },
            "chain": {"type": "string", "description": "Blockchain to query (default: base)"},
        }

    info = {
        "input": info_input,
        "output": {
            "type": "json",
            "example": {
                "data": {"result": "tool output"},
                "sources_used": ["source1", "source2"],
            },
        },
        "category": bazaar_category,
        "tags": ["crypto", "security", "blockchain", tool_id],
        "description": pricing.get("description", f"{tool_id} — Rug Munch Intelligence"),
    }

    # Schema that validates the info structure per bazaar spec
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "input": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "const": "http"},
                    "method": {
                        "type": "string",
                        "enum": ["GET", "HEAD", "DELETE"]
                        if http_method == "GET"
                        else ["POST", "PUT", "PATCH"],
                    },
                    "queryParams": {"type": "object"}
                    if http_method == "GET"
                    else {"type": "object"},
                    "bodyType": {"type": "string", "enum": ["json", "form-data", "text"]},
                    "body": {"type": "object"},
                },
                "required": ["type", "method"],
                "additionalProperties": False,
            },
            "output": {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "example": {"type": "object"},
                },
                "required": ["type"],
            },
            "category": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "description": {"type": "string"},
        },
        "required": ["input"],
    }

    return {
        "info": info,
        "schema": schema,
    }


def _build_resource_info(tool_id: str, pricing: dict) -> dict:
    """Build x402 v2 ResourceInfo object for PaymentRequired response.

    Per x402 v2 spec section 5.1.2, ResourceInfo has only three fields:
    - url (required): URL of the protected resource
    - description (optional): Human-readable description
    - mimeType (optional): MIME type of the expected response

    NOTE: serviceName, tags, and iconUrl are NOT part of the spec's ResourceInfo.
    The v2 spec does NOT include serviceName/tags/iconUrl at the resource level.
    We preserve them as legacy compat fields in x402.resource_metadata for our
    own internal use, but they must not appear in the spec-compliant ResourceInfo.
    """
    description = pricing.get("description", f"Rug Munch Intelligence — {tool_id}")
    # Per spec: max 200 chars for description
    if len(description) > 200:
        description = description[:197] + "..."

    return {
        "url": f"https://mcp.rugmunch.io/api/v1/x402-tools/{tool_id}",
        "description": description,
        "mimeType": "application/json",
    }


# ── 402 response builder ──


def build_402_response(tool_id: str, client_id: str = "") -> JSONResponse:
    """Build an x402 v2 compliant Payment Required response.

    Returns both:
    - V2 spec format (x402Version, resource, accepts) for official SDK compatibility
    - Legacy format (error, tool, price, x402.requirements) for backward compat
    - PAYMENT-REQUIRED base64 header for @x402/fetch and x402 Python SDK

    The v2 spec requires:
    - Top-level x402Version: 2
    - Top-level resource: {url, description, mimeType, serviceName, tags, iconUrl}
    - Top-level accepts: [{scheme, network, asset, amount, payTo, maxTimeoutSeconds, extra}]
    - PAYMENT-REQUIRED header: base64 JSON of {x402Version, resource, accepts}
    - Optional extensions: {bazaar: {discoverable, category, tags, inputSchema, outputSchema}}
    """
    # Ensure pay-to addresses are loaded before building 402 response (added 2026-06-25)
    _ensure_pay_addresses()
    pricing = TOOL_PRICES.get(tool_id, {"price_usd": 0.01, "price_atoms": "10000", "trial_free": 3})
    trial_free = pricing.get("trial_free", 3)

    # Check actual remaining trials for this client
    remaining = 0
    if client_id:
        _can_use, remaining = check_trial(tool_id, client_id)

    # Build v2 spec accepts array (shared by both body and header)
    accepts = _build_accepts_list(tool_id, pricing)

    # Build v2 spec resource object
    resource = _build_resource_info(tool_id, pricing)

    # Build bazaar extension for discovery indexing
    bazaar = _build_bazaar_extension(tool_id, pricing, accepts)

    # ── V2 spec body format (what official SDKs parse) ──
    v2_body = {
        "x402Version": 2,
        "error": "PAYMENT-SIGNATURE header is required",
        "resource": resource,
        "accepts": accepts,
        "extensions": {
            "bazaar": bazaar,
        },
        # ── Legacy compat fields (not in spec but needed for our gateway clients) ──
        "error_legacy": "Payment Required",
        "tool": tool_id,
        "price": f"${pricing['price_usd']:.2f}",
        "trial_free": trial_free,
        "trial_remaining": remaining,
        "trial_used": remaining <= 0,
        "wallet_required": remaining == -1,
        "message": (
            f"Connect a wallet to continue. Your 1 free trial is used — link MetaMask or Phantom to get {trial_free} free calls per tool. From ${pricing['price_usd']:.2f}/call after that."
            if remaining == -1
            else f"All {trial_free} free trial{'s' if trial_free != 1 else ''} used. Pay {pricing['price_atoms']} atoms to use {tool_id}. Pay on {', '.join(CHAIN_NAMES.get(c, c) for c in CHAIN_USDC)}."
        ),
        "accepted_chains": list(CHAIN_USDC.keys()),
        "chain_details": {
            k: {
                "network": v["network"],
                "facilitators": v.get("facilitators", []),
                "tokens": list(v.get("tokens", {"USDC": v.get("usdc", "")}).keys()),
            }
            for k, v in CHAIN_USDC.items()
        },
        # ── Legacy compat: nested requirements for our Cloudflare workers ──
        "x402": {
            "version": "2",
            "requirements": accepts,  # Same array, different field name for backward compat
        },
    }

    # ── Build PAYMENT-REQUIRED header (base64 JSON of v2 spec PaymentRequired) ──
    payment_required_header_obj = {
        "x402Version": 2,
        "error": "PAYMENT-SIGNATURE header is required",
        "resource": resource,
        "accepts": accepts,
        "extensions": {"bazaar": bazaar},
    }
    payment_required_b64 = _base64.b64encode(
        json.dumps(payment_required_header_obj, separators=(",", ":")).encode()
    ).decode("ascii")

    return JSONResponse(
        status_code=402,
        content=v2_body,
        headers={
            "PAYMENT-REQUIRED": payment_required_b64,
            "X-Paywall-Version": "2",
            "Content-Type": "application/json",
            **SECURITY_HEADERS,
        },
    )


# ── Payment header parser (supports v1 x-pay and v2 PAYMENT-SIGNATURE) ──


def parse_x_pay_header(header_value: str) -> dict | None:
    """Parse payment payload from x-pay / PAYMENT-SIGNATURE header.

    Supports three formats:
    1. v2 spec: base64-encoded JSON (PAYMENT-SIGNATURE header)
    2. v1 legacy: "x402 <json>" or "X-Pay: <json>" (x-pay header)
    3. Plain JSON
    """
    if not header_value or not header_value.strip():
        return None
    try:
        stripped = header_value.strip()

        # v2 spec: PAYMENT-SIGNATURE is base64-encoded JSON
        # Try base64 decode first (will fail fast if it's not base64)
        try:
            decoded = _base64.b64decode(stripped).decode("utf-8")
            if decoded.startswith("{"):
                payload = json.loads(decoded)
                # v2 format: normalize PaymentPayload to our expected structure
                # v2 PaymentPayload has: x402Version, resource, accepted, payload, extensions
                # Our verifier expects: accepted (dict), payload (with signature/authorization)
                if "x402Version" in payload:
                    # v2 PaymentPayload — our verifier already handles this format
                    # It extracts network from payload.accepted and routes to facilitator
                    return payload
                return payload
        except Exception:
            pass  # Not base64, try other formats

        # v1 legacy: "x402 <json>" or "X-Pay: <json>"
        if stripped.startswith("x402 "):
            payload_str = stripped[5:].strip()
        elif stripped.startswith("X-Pay: "):
            payload_str = stripped[7:].strip()
        else:
            payload_str = stripped

        if payload_str.startswith("{"):
            return json.loads(payload_str)

        return None
    except Exception as e:
        logger.warning(f"Failed to parse payment header: {e}")
        return None


# ── Verification via Facilitator Router ──

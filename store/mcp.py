"""MCP JSON-RPC surface — tools/list, tools/call, initialize.

Pay-per-call is enforced at the HTTP layer: a tool call that cannot be
authorised returns HTTP 402 carrying the x402 challenge in both the
``PAYMENT-REQUIRED`` header and the JSON-RPC error ``data``, so a plain MCP
client and an x402 client can both proceed.
"""

from __future__ import annotations

import json
from typing import Any

from store import capabilities
from store.catalog import TOOLS, get_tool
from store.challenge import encode_challenge
from store.config import Settings
from store.gate import authorize
from store.store_db import StoreDB

PROTOCOL_VERSION = "2026-07-28"
PAYMENT_REQUIRED_CODE = -32002


def _tool_descriptor(tool_name: str) -> dict[str, Any]:
    """One MCP tool entry with price + free tier in its annotations."""
    tool = get_tool(tool_name)
    assert tool is not None
    return {
        "name": tool.name,
        "title": tool.title,
        "description": (
            f"{tool.description} Price: ${tool.price_usd:.2f} per call via x402 "
            f"(Base USDC), free tier {tool.free_daily}/day."
        ),
        "inputSchema": tool.input_schema,
        "outputSchema": tool.output_schema,
        "annotations": {
            "priceUsd": tool.price_usd,
            "priceAtoms": str(tool.price_atoms),
            "freeTierCallsPerDay": tool.free_daily,
            "tier": tool.tier,
            "paymentProtocol": "x402",
        },
    }


def _error(rpc_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": rpc_id, "error": err}


async def _call_tool(
    params: dict[str, Any],
    headers: dict[str, str],
    client_ip: str,
    db: StoreDB,
    settings: Settings,
) -> tuple[int, dict[str, Any], dict[str, str]]:
    """Authorise then execute one tools/call."""
    rpc_id = params.get("_rpc_id")
    name = str(params.get("name") or "")
    arguments = params.get("arguments") or {}
    tool = get_tool(name)
    if tool is None:
        return 404, _error(rpc_id, -32602, f"unknown tool: {name}"), {}
    if not isinstance(arguments, dict):
        return 400, _error(rpc_id, -32602, "arguments must be an object"), {}

    decision = await authorize(tool, headers, client_ip, db, settings)
    if not decision.allowed:
        extra = {"X-PAYMENT-OPTIONS": "x402"}
        if decision.challenge:
            extra["PAYMENT-REQUIRED"] = encode_challenge(decision.challenge)
        data = {
            "reason": decision.reason,
            "tool": tool.name,
            "priceUsd": tool.price_usd,
            "receipt": decision.receipt,
            "challenge": decision.challenge,
        }
        return decision.status, _error(rpc_id, PAYMENT_REQUIRED_CODE, decision.reason, data), extra

    try:
        payload = await capabilities.call_capability(
            tool.capability,
            arguments,
            backend_url=settings.backend_url,
            timeout=settings.timeout_seconds,
        )
    except (ValueError, KeyError, TypeError) as exc:
        return 400, _error(rpc_id, -32602, f"invalid arguments: {exc}"), {}
    except Exception as exc:  # upstream failure must not be billed as success
        return 502, _error(rpc_id, -32603, f"capability failed: {type(exc).__name__}"), {}

    result = {
        "content": [{"type": "text", "text": json.dumps(payload, default=str)}],
        "structuredContent": payload,
        "receipt": decision.receipt,
        "isError": False,
    }
    return 200, {"jsonrpc": "2.0", "id": rpc_id, "result": result}, {}


async def handle_rpc(
    payload: dict[str, Any],
    headers: dict[str, str],
    client_ip: str,
    db: StoreDB,
    settings: Settings,
) -> tuple[int, dict[str, Any] | None, dict[str, str]]:
    """Dispatch one JSON-RPC message. Returns (status, body, extra headers)."""
    method = str(payload.get("method") or "")
    rpc_id = payload.get("id")
    params = payload.get("params") or {}
    if not isinstance(params, dict):
        params = {}

    if method == "initialize":
        return (
            200,
            {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "result": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "rmi-intelligence-store", "version": "1.0.0"},
                },
            },
            {},
        )

    if method in {"notifications/initialized", "notifications/cancelled"}:
        return 202, None, {}

    if method == "ping":
        return 200, {"jsonrpc": "2.0", "id": rpc_id, "result": {}}, {}

    if method == "tools/list":
        return (
            200,
            {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "result": {
                    "tools": [_tool_descriptor(t.name) for t in TOOLS],
                },
            },
            {},
        )

    if method == "tools/call":
        params["_rpc_id"] = rpc_id
        return await _call_tool(params, headers, client_ip, db, settings)

    return 400, _error(rpc_id, -32601, f"method not found: {method}"), {}

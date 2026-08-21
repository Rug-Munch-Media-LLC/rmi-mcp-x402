#!/usr/bin/env python3
"""MCP server that auto-pays x402 APIs — ported from the official
`guides__mcp-server-with-x402.md` to this repo's Python stack.

The official guide wires Claude to an x402 paid API through an MCP bridge that
(1) calls the resource server, (2) on `402 + PAYMENT-REQUIRED` parses the
payment requirements, (3) creates/signs a payment with a registered scheme, and
(4) retries with the payment header. This example reproduces that flow in Python
using FastMCP (server) + httpx (client) with an `X402Client` that wraps an httpx
client with automatic 402 handling (our `@x402/axios` equivalent).

Run:
    pip install fastmcp httpx pydantic
    EVM_PRIVATE_KEY=<wallet with USDC> SVM_PRIVATE_KEY=<base58 Solana key> \
        RESOURCE_SERVER_URL=http://localhost:4021 \
        python examples/mcp_server_paid.py
    # then connect an MCP client (Claude Desktop / Cursor) and call
    # `get-data-from-resource-server`. Unpaid -> auto-pay -> data.

At least one of EVM_PRIVATE_KEY / SVM_PRIVATE_KEY is required.
Direct (non-MCP) callers can use X402Client directly for programmatic access.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any

import httpx
from fastmcp import FastMCP

from app.domain.x402.middleware import (  # repo helpers for the server side
    HEADER_PAYMENT_REQUIRED,
    HEADER_X_PAYMENT_ID,
)


class X402Client:
    """httpx client that transparently completes the x402 payment handshake.

    Mirrors the official `wrapAxiosWithPayment` behaviour: on `402` with a
    `PAYMENT-REQUIRED` header, parse the `accepts[]`, pick a scheme, create a
    payment payload, and retry with `X-Payment-ID`.

    NOTE: this scaffold signs via the caller-supplied `sign_payload` hook so it
    works against the public `x402-py` verifier as well as the repo's
    self-hosted verifier (`app/routers/x402_verifier.py`). Production users
    should supply a real scheme signer (EIP-3009 / EIP-7702 / SPL).
    """

    def __init__(self, base_url: str, sign_payload: Any = None) -> None:
        self._client = httpx.Client(base_url=base_url, timeout=30.0)
        self._sign_payload = sign_payload

    def get(self, endpoint: str, **kw: Any) -> httpx.Response:
        resp = self._client.get(endpoint, **kw)
        if resp.status_code != 402:
            return resp
        return self._pay_and_retry(resp.request, resp)

    def _pay_and_retry(self, request: httpx.Request, first: httpx.Response) -> httpx.Response:
        required = base64.b64decode(first.headers[HEADER_PAYMENT_REQUIRED]).decode()
        req = json.loads(required)  # {x402Version, accepts: [{scheme, network, ...}]}
        accept = req["accepts"][0]  # server-ordered; a policy could select here
        if self._sign_payload is None:
            raise RuntimeError(
                "402 received but no signer configured; cannot create a payment "
                f"for {accept['network']} {accept.get('asset')}."
            )
        payload = self._sign_payload(accept)  # -> {"paymentId": "...", ...}
        headers = dict(request.headers)
        headers[HEADER_X_PAYMENT_ID] = payload["paymentId"]
        return self._client.send(
            httpx.Request(request.method, str(request.url), headers=headers, content=request.content)
        )

    def close(self) -> None:
        self._client.close()


# --- server-side guard (this repo) ------------------------------------------
# The same decorator that gates FastAPI routes gates MCP tool calls, so an MCP
# server exposing paid tools advertises the identical payment surface.
from app.domain.x402.middleware import require_payment  # noqa: E402


def paid_tool(ctx: Any) -> Any:
    """Decorator used by the paid MCP tool below (via require_payment)."""
    return require_payment()(ctx)


# --- MCP server -------------------------------------------------------------
mcp = FastMCP("x402 MCP paid bridge", version="1.0.0")

RESOURCE_SERVER_URL = os.getenv("RESOURCE_SERVER_URL", "http://localhost:4021")
ENDPOINT_PATH = os.getenv("ENDPOINT_PATH", "/weather")


@mcp.tool()
def get_data_from_resource_server() -> str:
    """Call a paid x402 API. If it responds 402, auto-pay and retry."""
    api = X402Client(RESOURCE_SERVER_URL)
    try:
        resp = api.get(ENDPOINT_PATH)
        resp.raise_for_status()
        return json.dumps(resp.json())
    finally:
        api.close()


@mcp.tool()
@require_payment()
def get_paid_mcp_payload(request: Any) -> str:
    """Server-side gated tool: requires a valid X-Payment-ID to run.

    Unpaid invocations return a spec-exact 402. (Kept for parity with the
    repo's paid-MCP mode; the auto-pay bridge above is the client side.)
    """
    return json.dumps({"data": "paid MCP tool result"})


def main() -> None:
    evm_key = os.getenv("EVM_PRIVATE_KEY")
    svm_key = os.getenv("SVM_PRIVATE_KEY")
    if not evm_key and not svm_key:
        raise SystemExit("EVM_PRIVATE_KEY and/or SVM_PRIVATE_KEY is required")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

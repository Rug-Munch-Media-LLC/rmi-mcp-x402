#!/usr/bin/env python3
"""Coffer: MCP server with a paid tool — ported from the official
`guides__mcp-server-with-x402.md` to Coffer's Python stack.

Two halves, mirroring the official guide:

  * client side — an MCP bridge that auto-pays an x402 API when it gets a 402
    (the official @x402/axios wrapper's job, reproduced with httpx);
  * server side — an MCP tool gated by `@coffer.require_payment`, so an agent
    pays for a tool result over MCP the native Coffer way.

Run:
    pip install fastmcp httpx pydantic
    EVM_PRIVATE_KEY=<wallet with USDC> SVM_PRIVATE_KEY=<base58 Solana key> \
        RESOURCE_SERVER_URL=http://localhost:4021 \
        python examples/mcp_server_paid.py
    # connect an MCP client (Claude Desktop / Cursor) and call
    # `get_data_from_resource_server`. Unpaid -> auto-pay -> data.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any

import httpx
from _coffer_facade import require_payment  # brand facade (Coffer)
from fastmcp import FastMCP

HEADER_PAYMENT_REQUIRED = "PAYMENT-REQUIRED"
HEADER_X_PAYMENT_ID = "X-Payment-ID"


class X402Client:
    """httpx client that transparently completes the x402 payment handshake.

    Mirrors the official `wrapAxiosWithPayment` behaviour: on `402` with a
    `PAYMENT-REQUIRED` header, parse `accepts[]`, pick a scheme, create a
    payment payload, and retry with `X-Payment-ID`.

    NOTE: production callers should supply a real scheme signer (EIP-3009 /
    EIP-7702 / SPL) via `sign_payload`.
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
            httpx.Request(
                request.method, str(request.url), headers=headers, content=request.content
            )
        )

    def close(self) -> None:
        self._client.close()


mcp = FastMCP("Coffer MCP paid bridge", version="1.0.0")

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
def get_paid_mcp_payload() -> str:
    """Server-side gated tool: requires a valid X-Payment-ID to run.

    Unpaid invocations return a spec-exact 402. (Parity with Coffer's paid-MCP
    mode; the auto-pay bridge above is the client side.)
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

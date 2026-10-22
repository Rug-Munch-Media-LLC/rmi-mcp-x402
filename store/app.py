"""FastAPI wiring for the Rug Munch Intelligence MCP store.

Free endpoints: /healthz, /.well-known/x402, /store/catalog.
Paid endpoint: POST /mcp  (tools/call gated per call)
Admin endpoints (STORE_ADMIN_KEY): /store/ledger, /store/keys.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

from store import apikeys, coffer
from store.catalog import TOOLS
from store.catalog_render import catalog_payload
from store.config import Settings, load_settings
from store.mcp import handle_rpc
from store.store_db import StoreDB

log = logging.getLogger("store.app")

_settings: Settings = load_settings()
_db: StoreDB | None = None


def get_db() -> StoreDB:
    """Lazily construct the single StoreDB instance."""
    global _db
    if _db is None:
        _db = StoreDB(_settings.db_path)
    return _db


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    get_db()
    log.info("store_ready db=%s tools=%d", _settings.db_path, len(TOOLS))
    yield


app = FastAPI(
    title="Rug Munch Intelligence MCP Store",
    version="1.0.0",
    description="Paid MCP tool surface — x402 per call, Coffer as facilitator.",
    lifespan=lifespan,
)


@app.get("/healthz")
async def healthz() -> JSONResponse:
    """Liveness plus facilitator reachability."""
    fac = await coffer.health(_settings.coffer_url)
    body = {
        "status": "ok" if fac.get("reachable") else "degraded",
        "store": "rmi-intelligence",
        "tools": len(TOOLS),
        "facilitator": {"name": "coffer", **fac},
        "db": str(_settings.db_path),
        "freeTierEnabled": _settings.free_tier_enabled,
    }
    return JSONResponse(body, status_code=200 if fac.get("reachable") else 503)


@app.get("/store/catalog")
async def store_catalog() -> JSONResponse:
    """Free, machine-readable catalog: every tool, price, free tier, schemas."""
    payload = catalog_payload()
    payload["payment"] = {
        "protocol": "x402",
        "x402Version": 1,
        "facilitator": "coffer",
        "facilitatorUrl": _settings.coffer_url,
        "network": _settings.network,
        "asset": _settings.asset_contract,
        "payTo": _settings.pay_to,
        "testnet": {"network": _settings.testnet_network, "asset": _settings.testnet_asset},
        "endpoint": f"{_settings.public_base_url}/mcp",
    }
    return JSONResponse(payload)


@app.get("/.well-known/x402")
async def well_known() -> JSONResponse:
    """Discovery document for crawlers and agent registries."""
    return JSONResponse(
        {
            "x402Version": 1,
            "protocol": "x402",
            "name": "Rug Munch Intelligence",
            "description": "Paid crypto intelligence tools over MCP; pay per call with x402.",
            "facilitator": {
                "name": "coffer",
                "url": _settings.coffer_url,
                "verify": "/v1/x402/verify",
                "settle": "/v1/x402/settle",
                "health": "/v1/x402/health",
            },
            "mcp": {
                "endpoint": f"{_settings.public_base_url}/mcp",
                "transport": "http-jsonrpc",
                "protocolVersion": "2026-07-28",
            },
            "catalog": f"{_settings.public_base_url}/store/catalog",
            "payment": {
                "network": _settings.network,
                "asset": _settings.asset_contract,
                "payTo": _settings.pay_to,
                "header": "X-PAYMENT",
            },
            "tools": [
                {"name": t.name, "priceUsd": t.price_usd, "freeTierCallsPerDay": t.free_daily}
                for t in TOOLS
            ],
        }
    )


@app.post("/mcp")
async def mcp_endpoint(request: Request) -> JSONResponse:
    """MCP JSON-RPC. tools/call is metered and returns 402 when unpaid."""
    try:
        payload = await request.json()
    except ValueError:
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}},
            status_code=400,
        )
    if not isinstance(payload, dict):
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}},
            status_code=400,
        )
    headers = {k: v for k, v in request.headers.items()}
    client_ip = request.client.host if request.client else ""
    status, body, extra = await handle_rpc(payload, headers, client_ip, get_db(), _settings)
    if body is None:
        return JSONResponse(None, status_code=status, headers=extra)
    return JSONResponse(body, status_code=status, headers=extra)


def _admin_ok(admin_key: str | None) -> bool:
    return (
        bool(_settings.admin_key)
        and bool(admin_key)
        and apikeys.constant_time_equal(admin_key, _settings.admin_key)
    )


@app.get("/store/ledger")
async def ledger(
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
) -> JSONResponse:
    """Admin: recent settled receipts."""
    if not _admin_ok(x_admin_key):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return JSONResponse({"receipts": get_db().recent_receipts(100)})


@app.post("/store/keys")
async def create_key(
    payload: dict[str, Any], x_admin_key: str | None = Header(default=None, alias="X-Admin-Key")
) -> JSONResponse:
    """Admin: issue a prepaid API key. The raw key is returned exactly once."""
    if not _admin_ok(x_admin_key):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    label = str(payload.get("label") or "unnamed")
    usd = float(payload.get("usd") or 0)
    if usd <= 0:
        return JSONResponse({"error": "usd must be positive"}, status_code=400)
    raw = apikeys.issue_key()
    get_db().create_key(apikeys.hash_key(raw), label, int(round(usd * 1_000_000)))
    return JSONResponse({"apiKey": raw, "label": label, "balanceUsd": usd})

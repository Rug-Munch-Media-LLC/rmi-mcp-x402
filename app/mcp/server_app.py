"""X402MCPServer — FastAPI application for the x402 MCP gateway.

Wires together the tool manager, facilitator, and AI Guard into a
FastAPI app with system, discovery, and payment middleware.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app.mcp.ai_guard import AIGuardWrapper
from app.mcp.facilitator import MultiChainFacilitator
from app.mcp.models import X402Bundle
from app.mcp.tool_manager import X402ToolManager

logger = logging.getLogger("x402_mcp_server")


class X402MCPServer:
    """Complete x402 MCP server v5.0."""

    def __init__(self):
        self._tool_manager = X402ToolManager()
        self._facilitator = MultiChainFacilitator()
        self._ai_guard = AIGuardWrapper()
        self._app = None

        b = self._tool_manager
        logger.info("🎉 x402 MCP v5.0 initialized")
        logger.info(f"   Bundles: {len(b.list_bundles())}")
        logger.info(
            f"   Free: {len(b.free_bundles)} | Trial: {len(b.trial_bundles)} | Premium: {len(b.premium_bundles)}"
        )
        logger.info(f"   Endpoints: {b.total_endpoints}")
        logger.info(f"   Revenue/call: ${sum(b.price_usd for b in b.list_bundles()):.2f}")
        logger.info(f"   AI Guard: {'✅ Active' if self._ai_guard.active else '❌ Inactive'}")

    def streamable_http_app(self) -> FastAPI:
        if self._app is not None:
            return self._app

        app = FastAPI(
            title="RugMunch Intelligence x402 Gateway v5.0",
            description="Complete crypto security & intelligence x402 gateway with free/trial/premium tiers",
            version="5.0.0",
            docs_url="/",
        )

        self._add_system_endpoints(app)
        self._add_discovery_endpoints(app)
        self._add_middleware(app)

        self._app = app
        return app

    def _add_system_endpoints(self, app: FastAPI):
        """Health, status, version endpoints."""

        @app.get("/health")
        async def health():
            b = self._tool_manager
            return JSONResponse(
                {
                    "status": "healthy",
                    "version": "5.0.0",
                    "timestamp": datetime.utcnow().isoformat(),
                    "statistics": {
                        "bundles": len(b.list_bundles()),
                        "free": len(b.free_bundles),
                        "trial": len(b.trial_bundles),
                        "standard": len([x for x in b.list_bundles() if x.tier == "standard"]),
                        "premium": len(b.premium_bundles),
                        "endpoints": b.total_endpoints,
                    },
                    "ai_guard": self._ai_guard.active,
                    "chains": ["base", "solana"],
                    "facilitator": {
                        "base_wallet": self._facilitator.base_config["wallet_address"][:20] + "...",
                        "solana_wallet": self._facilitator.solana_config["wallet_address"][:20]
                        + "...",
                    },
                }
            )

        @app.get("/version")
        async def version():
            return JSONResponse(
                {
                    "version": "5.0.0",
                    "name": "RugMunch Intelligence x402 Gateway",
                    "chains": ["base", "solana"],
                    "tiers": ["free", "trial", "standard", "premium"],
                }
            )

    def _add_discovery_endpoints(self, app: FastAPI):
        """x402 discovery and bundle listing."""

        @app.get("/.well-known/x402")
        async def discovery():
            b = self._tool_manager
            return JSONResponse(
                {
                    "version": "5.0.0",
                    "name": "RugMunch Intelligence x402 Gateway",
                    "description": "Complete crypto security & intelligence with free/trial/premium tiers",
                    "facilitators": {
                        "base": self._facilitator.base_config,
                        "solana": self._facilitator.solana_config,
                        "third_party": self._facilitator.third_party,
                    },
                    "bundles": [bundle.dict() for bundle in b.list_bundles()],
                    "tiers": {
                        "free": [bundle.dict() for bundle in b.free_bundles],
                        "trial": [bundle.dict() for bundle in b.trial_bundles],
                        "premium": [bundle.dict() for bundle in b.premium_bundles],
                    },
                    "statistics": {
                        "total_bundles": len(b.list_bundles()),
                        "total_endpoints": b.total_endpoints,
                        "free_count": len(b.free_bundles),
                        "trial_count": len(b.trial_bundles),
                        "premium_count": len(b.premium_bundles),
                        "max_revenue_usd": f"${sum(x.price_usd for x in b.list_bundles()):.2f}",
                    },
                    "mcp_servers": {
                        "solana": "/mcp/solana",
                        "ethereum": "/mcp/eth",
                    },
                    "documentation": "https://docs.rugmunch.io/x402-v5",
                    "support": "support@rugmunch.io",
                }
            )

        @app.get("/v1/bundles")
        async def list_bundles(tier: str | None = None):
            bundles = self._tool_manager.list_bundles(tier)
            return JSONResponse(
                {
                    "success": True,
                    "count": len(bundles),
                    "tier_filter": tier,
                    "bundles": [b.dict() for b in bundles],
                }
            )

        @app.get("/v1/bundles/{bundle_id}")
        async def get_bundle(bundle_id: str):
            bundle = self._tool_manager.get_bundle(bundle_id)
            if not bundle:
                raise HTTPException(status_code=404, detail="Bundle not found")

            return JSONResponse(
                {
                    "success": True,
                    "bundle": bundle.dict(),
                    "payment_requirements": {
                        "base": self._get_payment_req(bundle, "base"),
                        "solana": self._get_payment_req(bundle, "solana"),
                    },
                }
            )

    def _add_middleware(self, app: FastAPI):
        """API key auth + AI Guard + x402 payment middleware."""

        # Read API key from env. If not set, auth is disabled (local dev).
        _api_key: str | None = os.environ.get("RMI_MCP_API_KEY")

        @app.middleware("http")
        async def security_and_payment_middleware(request: Request, call_next):
            path = request.url.path

            # Skip free/public endpoints
            if any(
                path.startswith(p)
                for p in [
                    "/health",
                    "/version",
                    "/.well-known",
                    "/docs",
                    "/openapi",
                    "/v1/bundles",
                ]
            ):
                return await call_next(request)

            # ── API Key authentication ──────────────────────────
            # Tier 2 moat: all non-public endpoints require a valid API key.
            if _api_key:
                req_key = request.headers.get("X-API-Key")
                if not req_key or req_key != _api_key:
                    return JSONResponse(
                        {
                            "error": "Unauthorized",
                            "code": "MISSING_API_KEY",
                            "message": "Valid X-API-Key header required. Set RMI_MCP_API_KEY on the server.",
                        },
                        status_code=401,
                        headers={"WWW-Authenticate": "ApiKey"},
                    )

            # AI Guard check
            if self._ai_guard.active:
                is_safe, reason = await self._ai_guard.check_request(request)
                if not is_safe:
                    return JSONResponse(
                        {
                            "error": "Request blocked by AI Guard",
                            "code": "AI_GUARD_BLOCKED",
                            "reason": reason,
                        },
                        status_code=403,
                    )

            # x402 payment check for premium bundles
            # Extract bundle from path
            path_parts = path.split("/")
            if len(path_parts) >= 2:
                bundle_id = (
                    path_parts[1]
                    if not path_parts[1].startswith("v1")
                    else path_parts[2]
                    if len(path_parts) > 2
                    else None
                )

                if bundle_id:
                    bundle = self._tool_manager.get_bundle(bundle_id)
                    if bundle and bundle.requires_payment and bundle.price_usd > 0:
                        x402_header = request.headers.get("X-Payment")
                        solana_payment = request.headers.get("X-Solana-Payment")

                        if not x402_header and not solana_payment:
                            return JSONResponse(
                                content={
                                    "error": "Payment required",
                                    "code": "PAYMENT_REQUIRED",
                                    "bundle": bundle_id,
                                    "bundle_name": bundle.name,
                                    "price_usd": bundle.price_usd,
                                    "payment_options": {
                                        "base": self._get_payment_req(bundle, "base"),
                                        "solana": self._get_payment_req(bundle, "solana"),
                                    },
                                    "instructions": "Include X-Payment (Base) or X-Solana-Payment (Solana) header with transaction proof",
                                },
                                status_code=402,
                                headers={
                                    "X-Payment-Required": "true",
                                    "X-Payment-Address-Base": self._facilitator.base_config[
                                        "wallet_address"
                                    ],
                                    "X-Payment-Address-Solana": self._facilitator.solana_config[
                                        "wallet_address"
                                    ],
                                    "X-Bundle-Name": bundle.name,
                                    "X-Price-USD": str(bundle.price_usd),
                                },
                            )

            return await call_next(request)

    def _get_payment_req(self, bundle: X402Bundle, chain: str) -> dict[str, Any]:
        facilitator = (
            self._facilitator.base_config if chain == "base" else self._facilitator.solana_config
        )

        if chain == "solana":
            amount_atomic = int(bundle.price_usd * 10**6)
            symbol = "USDC"
            decimals = 6
        else:
            amount_atomic = int(bundle.price_usd / 3000 * 10**18)
            symbol = "ETH"
            decimals = 18

        return {
            "chain": chain.upper(),
            "network": facilitator["network"],
            "amount_usd": bundle.price_usd,
            "amount_atomic": amount_atomic,
            "symbol": symbol,
            "decimals": decimals,
            "recipient_address": facilitator["wallet_address"],
            "reference": f"bundle-{bundle.bundle_id}",
            "valid_until": (datetime.utcnow() + timedelta(hours=1)).isoformat(),
        }

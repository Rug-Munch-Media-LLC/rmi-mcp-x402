"""x402 — thin consolidated router facade.

Mounts the spec-compliant x402 surface from the domain layer:

- ``app.domain.x402.router``  -> ``/v1/x402/*`` payment endpoints
  (``GET /payment``, ``POST /verify``, ``POST /settle``, ``GET /health``).
- ``app.domain.x402.tools_router`` -> ``/api/v1/x402-tools/*`` MCP tool catalog.

This replaces the 27 divergent ``app/routers/x402_*.py`` re-implementations
(which each shipped their own USDC / RPC / facilitator config copies). The
domain layer is the single source of truth; this module is just the mount
point. See ``docs/X402-GAP-AUDIT.md``.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.domain.x402.router import router as _domain_router
from app.domain.x402.tools_router import router as _tools_router

router = APIRouter()

# Spec-compliant 402-flow endpoints (GET /payment, POST /verify, POST /settle, GET /health)
router.include_router(_domain_router)

# Paid MCP tool catalog (mirrors /api/v1/x402/catalog but requires payment)
router.include_router(_tools_router)

# The three sellable paid offerings (Rug Verdict / Deployer Dossier /
# Cluster Attribution), each fail-closed x402-gated via the coffer
# facilitator client.
from app.domain.x402.offerings_router import router as _offerings_router  # noqa: E402

router.include_router(_offerings_router)

__all__ = ["router"]

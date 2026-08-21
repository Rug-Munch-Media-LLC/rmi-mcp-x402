"""x402 domain — auto-registers its health check + HTTP routes.

T34 from v4.0. Sovereign-first x402 payment layer for AI agents.

Re-exports the spec-conformant facilitator router + pricing/status surface
(:class:`FacilitatorRouter`, :func:`get_facilitator_router`,
:func:`register_facilitator`) and the legacy models (``PaymentFacilitator``,
``X402Tier``) for backward compatibility with v1 routers that import from
``app.domain.x402``.
"""
from __future__ import annotations

# Re-export models for backward compat with v1 routers and tests.
from app.domain.x402.models import (  # noqa: E402,F401  (re-export)
    PaymentFacilitator,
    PaymentReceipt,
    ToolCatalog,
    ToolCatalogEntry,
    ToolPricing,
    X402Tier,
)

# Spec-conformant facilitator surface + honest per-family verification status.
from app.domain.x402.service import (  # noqa: E402,F401  (re-export)
    VERIFICATION_STATUS,
    FacilitatorRouter,
    asset_to_atomic,
    get_facilitator_router,
    register_facilitator,
    resolve_price_usd,
    validate_client_amount,
)

# The T34 router is a cross-agent deliverable; expose it once importable so a
# mid-refactor router (or its not-yet-existing app.catalog dependency) cannot
# break package import for tests importing the service layer. [REVIEW]
try:
    from app.domain.x402.router import router  # type: ignore[import-not-found]  # noqa: E402,F401
except Exception:  # pragma: no cover - defensive; router is agent2's deliverable
    router = None  # type: ignore[assignment]

# Core health registration is guarded: app.core.health may not exist yet in a
# worktree mid-refactor (it is also referenced by app/mcp/server.py). [REVIEW]
try:
    from app.core.health import DomainHealth

    from app.core import health as _health_mod

    async def _health_check() -> DomainHealth:
        """x402 health: tools + middleware available + facilitator readiness."""
        try:
            from app.domain.x402.middleware import PRICING
            _router = get_facilitator_router()
            return DomainHealth(
                name="x402",
                healthy=True,
                details={
                    "tools": len(PRICING),
                    "middleware": "available",
                    "verification_status": dict(VERIFICATION_STATUS),
                    "facilitator_mode": _router.readiness().get("mode"),
                    "facilitator_ready": _router.readiness().get("ready"),
                },
            )
        except Exception as e:
            return DomainHealth(name="x402", healthy=False, error=str(e))

    _health_mod.register_health_check("x402", _health_check)
except ModuleNotFoundError:  # pragma: no cover - app.core.health not present yet
    pass

__all__ = [
    "FacilitatorRouter",
    "PaymentFacilitator",
    "PaymentReceipt",
    "ToolCatalog",
    "ToolCatalogEntry",
    "ToolPricing",
    "VERIFICATION_STATUS",
    "X402Tier",
    "asset_to_atomic",
    "get_facilitator_router",
    "register_facilitator",
    "resolve_price_usd",
    "router",
    "validate_client_amount",
]

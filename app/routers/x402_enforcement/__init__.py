"""x402_enforcement — payment enforcement middleware + discovery/refund endpoints.

Split from the former ``app.routers.x402_enforcement`` monolith into responsibility
modules. ``__init__`` re-exports the entire original public API so existing
``from app.routers.x402_enforcement import X`` imports keep working unchanged."""

from __future__ import annotations

# Shared constants / chain configs / verifier state
from app.routers.x402_enforcement.config import (
    CHAIN_NAMES,
    CHAIN_USDC,
    DISCOVERY_CACHE_TTL,
    EVM_PAY_TO,
    SECURITY_HEADERS,
    SOL_PAY_TO,
    TRIAL_REDIS_PREFIX,
    TRIAL_WINDOW_SECONDS,
    VERIFIER,
    get_client_id,  # noqa: F401
    get_redis,  # noqa: F401
)

# Routers
from app.routers.x402_enforcement.endpoints import discovery_router, router  # noqa: F401

# Middleware
from app.routers.x402_enforcement.middleware import x402_enforcement_middleware  # noqa: F401

# Policy helpers
from app.routers.x402_enforcement.policy import (  # noqa: F401
    _record_refundable_payment,
    check_trial,
)

# Pricing catalog
from app.routers.x402_enforcement.pricing import TOOL_PRICES, _ensure_tool_prices  # noqa: F401

# 402 spec builders
from app.routers.x402_enforcement.spec import (
    _build_accepts_list,  # noqa: F401
    _build_bazaar_extension,  # noqa: F401
    _build_resource_info,  # noqa: F401
    build_402_response,
    parse_x_pay_header,
)

# Verification
from app.routers.x402_enforcement.verification import (
    _detect_token_from_asset,  # noqa: F401
    self_verify_evm_usdc,  # noqa: F401
    verify_payment,
    verify_payment_via_router,
)

__all__ = [
    "CHAIN_NAMES",
    "CHAIN_USDC",
    "DISCOVERY_CACHE_TTL",
    "EVM_PAY_TO",
    "SECURITY_HEADERS",
    "SOL_PAY_TO",
    "TOOL_PRICES",
    "TRIAL_REDIS_PREFIX",
    "TRIAL_WINDOW_SECONDS",
    "VERIFIER",
    "build_402_response",
    "check_trial",
    "discovery_router",
    "get_client_id",
    "parse_x_pay_header",
    "router",
    "verify_payment",
    "verify_payment_via_router",
    "x402_enforcement_middleware",
]

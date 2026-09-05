# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Rug Munch Media LLC
# --- facilitator_router.py: thin facade over the split facilitator modules ---
#
# The original 517-line god file was split (pure code-move, no logic change):
#   facilitator_core.py    — router state, health/circuit-breaker, extension points
#   facilitator_verify.py  — payment verification paths (self-hosted, EVM, Solana, scaffolded)
#   facilitator_settle.py  — settlement paths (self-hosted, external, fail-closed fallbacks)
# This module re-exports the public surface so existing imports keep working.
from __future__ import annotations

from typing import Any

from .facilitator_core import FacilitatorCoreMixin
from .facilitator_settle import FacilitatorSettleMixin
from .facilitator_verify import FacilitatorVerifyMixin

__all__ = [
    "FacilitatorRouter",
    "_facilitator_router",
    "get_facilitator_router",
    "register_facilitator",
    "set_facilitator_router",
]


class FacilitatorRouter(FacilitatorVerifyMixin, FacilitatorSettleMixin, FacilitatorCoreMixin):
    """Smart router that picks the best facilitator per chain/asset.

    Falls back through enabled external facilitators with health-aware
    decimation (circuit breaker after 3 failures). ``self_hosted`` mode
    (default on) uses :class:`SelfFacilitator` + EIP-7702 + the Solana SPL RPC
    verifier INSTEAD of the external URLs — no Coinbase/CDP dependency.
    Replay protection burns the tx_hash via Redis SETNX (24h TTL).
    """


# ── Global router instance (mirrors x402-py accessors) ──────────────────────

_facilitator_router: FacilitatorRouter | None = None


def get_facilitator_router() -> FacilitatorRouter:
    """Return the global facilitator router (lazily initialised)."""
    global _facilitator_router
    if _facilitator_router is None:
        _facilitator_router = FacilitatorRouter()
    return _facilitator_router


def set_facilitator_router(router: FacilitatorRouter) -> None:
    """Override the global facilitator router (for tests)."""
    global _facilitator_router
    _facilitator_router = router


def register_facilitator(network: str, impl: Any) -> None:
    """Register a custom facilitator for ``network`` on the global router.

    Extension point — e.g. plug in a real TRON TRC-20 verifier once one exists
    (currently TRON is scaffolded). ``impl`` must expose
    ``async verify(payment) -> VerifyResult`` and ``async settle(payment) -> str | None``.
    """
    get_facilitator_router().register_facilitator(network, impl)

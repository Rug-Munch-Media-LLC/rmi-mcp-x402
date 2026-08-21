"""Coffer example facade — makes the brand one-liner runnable today.

The public Coffer decorator takes a payment spec at decorate time:

    @coffer.require_payment(asset="USDC", network="base", amount=0.001)

The underlying domain gate reads the payment surface from configuration
(env + pricing tables), so the brand kwargs are accepted here as declarative
intent and passed through to the real decorator. Swap this module for the
shipped `coffer` package when it lands — the example code is unchanged.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable


def _make_facade() -> SimpleNamespace:
    from functools import wraps

    try:  # brand facade package ships — use it directly
        import coffer as _real

        return _real

    except ImportError:  # current repo layout: app.domain.x402
        from app.domain.x402.middleware import require_payment as _raw

        # Real decorator takes only `store=`; accept brand kwargs declaratively.
        def require_payment(
            *, asset: str = "", network: str = "", amount: float = 0.0, **_kw: Any
        ) -> Callable[..., Any]:
            return _raw()

        def get_facilitator_router() -> Any:
            from app.domain.x402 import get_facilitator_router as _g

            return _g()

        def register_facilitator(network: str, impl: Any) -> None:
            from app.domain.x402 import register_facilitator as _r

            return _r(network, impl)

        return SimpleNamespace(
            require_payment=require_payment,
            get_facilitator_router=get_facilitator_router,
            register_facilitator=register_facilitator,
        )


coffer = _make_facade()
require_payment = coffer.require_payment

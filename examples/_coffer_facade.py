"""Coffer example facade — makes the brand one-liner runnable today.

The public Coffer decorator takes a payment spec at decorate time:

    @coffer.require_payment(asset="USDC", network="base", amount=0.001)

The underlying domain gate reads the payment surface from configuration
(env + pricing tables), so the brand kwargs are accepted as **declarative
intent** — they document the intended asset/network/amount while the real gate
(``require_payment`` = ``store`` only) applies the configured policy. This
facade strips those kwargs up front so the same example runs whether the
shipped ``coffer`` package is importable (root on ``PYTHONPATH``) or the repo
falls back to ``app.domain.x402`` directly. Swap this module for the shipped
``coffer`` package when its decorator accepts the spec kwargs — the example
code is unchanged.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable


def _strip_kwargs(
    raw: Callable[..., Callable[..., Any]],
) -> Callable[..., Callable[..., Any]]:
    """Return a decorator that accepts (and ignores) brand spec kwargs.

    The brand one-liner's ``asset`` / ``network`` / ``amount`` are declarative
    intent; the real gate resolves the payment surface from configuration.
    """
    from functools import wraps

    @wraps(raw)
    def require_payment(
        *,
        asset: str = "",  # noqa: ARG001 — declarative intent
        network: str = "",  # noqa: ARG001
        amount: float = 0.0,  # noqa: ARG001
        **_kw: Any,
    ) -> Callable[..., Any]:
        return raw()

    return require_payment


def _make_facade() -> SimpleNamespace:
    try:  # brand facade package is importable — use it, but accept brand kwargs
        import coffer as _real

        return SimpleNamespace(
            require_payment=_strip_kwargs(_real.require_payment),
            get_facilitator_router=_real.get_facilitator_router,
            register_facilitator=_real.register_facilitator,
        )

    except ImportError:  # current repo layout: app.domain.x402
        from app.domain.x402.middleware import require_payment as _raw

        def get_facilitator_router() -> Any:
            from app.domain.x402 import get_facilitator_router as _g

            return _g()

        def register_facilitator(network: str, impl: Any) -> None:
            from app.domain.x402 import register_facilitator as _r

            return _r(network, impl)

        return SimpleNamespace(
            require_payment=_strip_kwargs(_raw),
            get_facilitator_router=get_facilitator_router,
            register_facilitator=register_facilitator,
        )


coffer = _make_facade()
require_payment = coffer.require_payment

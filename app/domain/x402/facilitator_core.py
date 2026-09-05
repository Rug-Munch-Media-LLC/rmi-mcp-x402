# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Rug Munch Media LLC
# --- facilitator_core.py: router state, health/circuit-breaker, extension points ---
from __future__ import annotations

import logging
from typing import Any

from .facilitator_config import (
    DEFAULT_FACILITATORS,
    X402_SELF_FACILITATE,
    X402_SELF_FACILITATOR_KEY,
    X402_SELF_FACILITATOR_RPC,
    X402_WALLET,
    FacilitatorConfig,
)
from .pricing import network_family, normalize_network_id
from .replay import ReplayStore
from .self_facilitator import SelfFacilitator

log = logging.getLogger(__name__)


class FacilitatorCoreMixin:
    """Router state, health-aware failure tracking, and extension points.

    Falls back through enabled external facilitators with health-aware
    decimation (circuit breaker after 3 failures). ``self_hosted`` mode
    (default on) uses :class:`SelfFacilitator` + EIP-7702 + the Solana SPL RPC
    verifier INSTEAD of the external URLs — no Coinbase/CDP dependency.
    Replay protection burns the tx_hash via Redis SETNX (24h TTL).
    """

    def __init__(
        self,
        facilitators: list[FacilitatorConfig] | None = None,
        self_hosted: bool | None = None,
    ) -> None:
        self.facilitators = sorted(
            [f for f in (facilitators or DEFAULT_FACILITATORS) if f.enabled and f.verify_url],
            key=lambda f: f.priority,
        )
        self._failures: dict[str, int] = {}
        self.self_hosted = X402_SELF_FACILITATE if self_hosted is None else bool(self_hosted)
        self._self_facilitator: SelfFacilitator | None = None
        self._network_facilitators: dict[str, Any] = {}
        self._pending_auths: dict[str, dict[str, Any]] = {}
        self.replay = ReplayStore()

    # ── health / extension points ──────────────────────────────────────────

    def _is_healthy(self, config: FacilitatorConfig) -> bool:
        return self._failures.get(config.name, 0) < 3

    def _mark_failure(self, config: FacilitatorConfig) -> None:
        self._failures[config.name] = self._failures.get(config.name, 0) + 1
        log.warning("x402_facilitator_failure", extra={"facilitator": config.name})

    def _mark_success(self, config: FacilitatorConfig) -> None:
        self._failures.pop(config.name, None)

    def register_facilitator(self, network: str, impl: Any) -> None:
        """Register a custom facilitator impl for a network (e.g. TRON).

        ``impl`` must expose ``async verify(payment) -> VerifyResult`` and
        ``async settle(payment) -> str | None``.
        """
        network_id = normalize_network_id(network)
        self._network_facilitators[network_id] = impl
        if ":" not in network:
            self._network_facilitators[network] = impl

    def submit_authorization(self, payment_id: str, authorization: dict[str, Any]) -> None:
        self._pending_auths[payment_id] = authorization

    def readiness(self) -> dict[str, Any]:
        wallet = (X402_WALLET or "").strip()
        wallet_configured = bool(wallet and wallet != "coffer-unconfigured-wallet")
        mode = "self_hosted" if self.self_hosted else "external"
        if self.self_hosted:
            fac = self._get_self_facilitator()
            ready = wallet_configured and bool(fac.rpc_url or fac.pay_to)
        else:
            ready = wallet_configured and any(f.enabled and f.verify_url for f in self.facilitators)
        return {"mode": mode, "ready": ready, "wallet_configured": wallet_configured}

    def settlement_stats(self) -> dict[str, Any]:
        fac = self._get_self_facilitator()
        return {"nonces_seen": len(fac.nonces), "pending": len(self._pending_auths)}

    def _impl_for(self, network: str) -> Any | None:
        network_id = normalize_network_id(network)
        family = network_family(network)
        return (
            self._network_facilitators.get(network_id)
            or self._network_facilitators.get(network)
            or self._network_facilitators.get(family)
        )

    def _get_self_facilitator(self) -> SelfFacilitator:
        if self._self_facilitator is None:
            self._self_facilitator = SelfFacilitator(
                pay_to=X402_WALLET,
                facilitator_key=X402_SELF_FACILITATOR_KEY,
                rpc_url=X402_SELF_FACILITATOR_RPC,
            )
        return self._self_facilitator

    def _build_payment(
        self, payment_id: str, tx_hash: str, network: str, asset: str, amount_usd: float
    ) -> dict[str, Any]:
        payment: dict[str, Any] = {
            "payment_id": payment_id,
            "tx_hash": tx_hash,
            "network": network,
            "asset": asset,
            "amount_usd": amount_usd,
        }
        auth = self._pending_auths.get(payment_id)
        if auth:
            payment["authorization"] = auth
        return payment

    def _replay_key(self, tx_hash: str, network: str) -> str:
        return f"x402:replay:{normalize_network_id(network)}:{str(tx_hash).strip().lower()}"

    async def _enforce_replay(self, tx_hash: str, network: str) -> tuple[bool, str]:
        """Burn the tx_hash nonce. Returns (ok, reason); ok=False => replay."""
        if not tx_hash:
            return True, ""
        key = self._replay_key(tx_hash, network)
        if not await self.replay.check_and_burn(key):
            return False, "transaction already used (replay)"
        return True, ""

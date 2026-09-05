# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Rug Munch Media LLC
# --- facilitator_settle.py: settlement paths (self-hosted, external, fail-closed fallbacks) ---
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from .external_verify import normalize_settlement
from .facilitator_config import (
    X402_NETWORK_WALLETS,
    X402_OFFLINE_MODE,
    X402_TIMEOUT_SECONDS,
    X402_WALLET,
)
from .pricing import asset_to_atomic, network_family, normalize_network_id

log = logging.getLogger(__name__)


class FacilitatorSettleMixin:
    """Settlement: self-hosted broadcast, external facilitators, fail-closed."""

    # ── settle ─────────────────────────────────────────────────────────────

    async def settle(
        self,
        payment_id: str,
        tx_hash: str,
        network: str,
        asset: str,
        amount_usd: float,
    ) -> dict[str, Any]:
        """Confirm settlement with the best available facilitator."""
        network_id = normalize_network_id(network)
        if X402_OFFLINE_MODE:
            return {
                "success": True,
                "transaction": tx_hash,
                "network": network_id,
                "payment_id": payment_id,
                "facilitator": "offline",
                "settled_at": datetime.now(UTC).isoformat(),
                "note": "Offline mode",
            }

        if self.self_hosted or self._impl_for(network_id) is not None:
            return await self._settle_self_hosted(
                payment_id, tx_hash, network_id, asset, amount_usd
            )

        payload = {
            "paymentId": payment_id,
            "transactionHash": tx_hash,
            "network": network_id,
            "asset": asset,
            "amount": str(asset_to_atomic(amount_usd, asset, network_id)),
            "payToAddress": X402_NETWORK_WALLETS.get(network_id) or X402_WALLET,
            "scheme": "exact",
            "timestamp": datetime.now(UTC).isoformat(),
        }
        last_error = "No facilitators available"
        async with httpx.AsyncClient(timeout=X402_TIMEOUT_SECONDS) as client:
            for config in self.facilitators:
                if not self._is_healthy(config) or not config.settle_url:
                    continue
                try:
                    response = await client.post(config.settle_url, json=payload)
                    response.raise_for_status()
                    data = response.json()
                    self._mark_success(config)
                    settled = normalize_settlement(
                        data, payment_id, tx_hash, network_id, config.name
                    )
                    from app.domain.x402.observability import observe_settle

                    observe_settle(settled, payment_id=payment_id, tx_hash=tx_hash)
                    return settled
                except httpx.HTTPStatusError as e:
                    last_error = f"{config.name}: HTTP {e.response.status_code}"
                    self._mark_failure(config)
                except httpx.RequestError as e:
                    last_error = f"{config.name}: {e}"
                    self._mark_failure(config)
                except json.JSONDecodeError as e:
                    last_error = f"{config.name}: invalid JSON - {e}"
                    self._mark_failure(config)
        if network_family(network_id) == "evm":
            # EIP-7702 self-VERIFY checks on-chain receipts; it does NOT
            # broadcast a settlement. Returning success here would fabricate
            # a settlement record (C1 honesty fix) -> fail closed.
            return {
                "success": False,
                "payment_id": payment_id,
                "reason": (
                    "needs_facilitator: no external facilitator settled and "
                    "no self-hosted settle key configured (EIP-7702 verifies "
                    "on-chain receipts but does not broadcast settlements)"
                ),
                "scaffolded": True,
                "network": network_id,
                "facilitator": "eip-7702-verify-only",
            }
        if network_family(network_id) in ("solana", "tron"):
            return {
                "success": False,
                "payment_id": payment_id,
                "reason": "needs_facilitator: settlement verification "
                "not implemented for this chain",
                "scaffolded": True,
                "network": network_id,
            }
        return {"success": False, "payment_id": payment_id, "reason": last_error}

    async def _settle_self_hosted(
        self,
        payment_id: str,
        tx_hash: str,
        network: str,
        asset: str,
        amount_usd: float,
    ) -> dict[str, Any]:
        network_id = normalize_network_id(network)
        impl = self._impl_for(network_id) or self._get_self_facilitator()
        payment = self._build_payment(payment_id, tx_hash, network_id, asset, amount_usd)
        try:
            settled_tx = await impl.settle(payment)
        except Exception as e:
            settled_tx = None
            log.warning("x402_self_settle_error", extra={"error": str(e)})
        if settled_tx:
            return {
                "success": True,
                "transaction": str(settled_tx),
                "network": network_id,
                "payment_id": payment_id,
                "facilitator": "self-hosted",
                "settled_at": datetime.now(UTC).isoformat(),
                "note": "Broadcast by self-hosted facilitator",
            }
        if network_family(network_id) == "solana":
            # SPL RPC verifies on-chain receipts; it does NOT broadcast a
            # settlement. Returning success here would fabricate a settlement
            # record (C2 honesty fix) -> fail closed.
            return {
                "success": False,
                "payment_id": payment_id,
                "reason": (
                    "needs_facilitator: solana SPL RPC verifies on-chain "
                    "receipts but does not broadcast settlements"
                ),
                "scaffolded": True,
                "network": network_id,
                "facilitator": "solana-rpc-verify-only",
            }
        return {
            "success": False,
            "payment_id": payment_id,
            "reason": "self-hosted settle unavailable: no authorization, "
            "no facilitator key/RPC, or chain unreachable",
            "facilitator": "self-hosted",
        }

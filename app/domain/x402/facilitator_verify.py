# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Rug Munch Media LLC
# --- facilitator_verify.py: payment verification paths (self-hosted, EVM, Solana, scaffolded) ---
from __future__ import annotations

import logging
import math
from typing import Any

from .evm_verify import _eip7702_verify
from .external_verify import verify_external, verify_solana
from .facilitator_config import X402_OFFLINE_MODE
from .models import VerifyResult
from .pricing import (
    SCAFFOLDED_NETWORKS,
    network_family,
    network_verification_status,
    normalize_network_id,
    resolve_asset_entry,
)
from .replay import REPLAY_HTTP_STATUS

log = logging.getLogger(__name__)


class FacilitatorVerifyMixin:
    """Payment verification: dispatch, self-hosted verify, scaffold fallbacks."""

    # ── verify ─────────────────────────────────────────────────────────────

    async def verify(
        self,
        payment_id: str,
        tx_hash: str,
        network: str,
        asset: str,
        amount_usd: float,
    ) -> dict[str, Any]:
        """Verify a payment with the best available facilitator.

        Returns ``{"verified", "reason", "settlement", "facilitator", ...}``.
        Replay-protected: a successfully-verified tx_hash is burned (SETNX,
        24h TTL) and cannot be reused.
        """
        network_id = normalize_network_id(network)

        # Reject invalid amounts before any network call.
        if amount_usd is None or not math.isfinite(float(amount_usd)) or amount_usd < 0:
            return {
                "verified": False,
                "reason": "invalid (non-finite/negative) amount",
                "settlement": None,
                "network": network_id,
            }

        if X402_OFFLINE_MODE:
            ok, reason = await self._enforce_replay(tx_hash, network_id)
            if not ok:
                return {
                    "verified": False,
                    "reason": reason,
                    "settlement": None,
                    "network": network_id,
                    "http_status": REPLAY_HTTP_STATUS,
                }
            return {"verified": True, "reason": "offline_mode", "settlement": None}

        # TRON: real TRC-20 USDT verifier wired (ported from the Pry
        # production verifier, 2026-09-01). Settlements are verified on-chain
        # via Trongrid gettransactioninfobyid — fail-closed on non-USDT
        # contracts, wrong recipient, or underpayment.
        if network_family(network_id) == "tron":
            return await verify_external(self, payment_id, tx_hash, network_id, asset, amount_usd)

        # Other scaffolded networks stay scaffolded — never claimed verified.
        if network_id in SCAFFOLDED_NETWORKS:
            return self._scaffold_non_evm_verify(payment_id, tx_hash, network_id, asset, amount_usd)

        # Self-hosted / custom impl path (default).
        if self.self_hosted or self._impl_for(network_id) is not None:
            result = await self._verify_self_hosted(
                payment_id, tx_hash, network_id, asset, amount_usd
            )
            if result.get("verified"):
                ok, reason = await self._enforce_replay(tx_hash, network_id)
                if not ok:
                    return {
                        "verified": False,
                        "reason": reason,
                        "settlement": None,
                        "network": network_id,
                        "http_status": REPLAY_HTTP_STATUS,
                    }
            return result

        # External facilitator path.
        verified_result = await verify_external(
            self, payment_id, tx_hash, network_id, asset, amount_usd
        )
        from app.domain.x402.observability import observe_verify
        from app.domain.x402.webhooks import emit_payment_event

        observe_verify(verified_result, payment_id=payment_id, tx_hash=tx_hash)
        emit_payment_event(
            "payment.verified",
            payment_id=payment_id,
            transaction=tx_hash,
            network=network_id,
            verified=bool(verified_result.get("verified")),
        )
        return verified_result

    async def _verify_self_hosted(
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
            result: Any = await impl.verify(payment)
        except Exception as e:
            result = VerifyResult(verified=False, reason=f"facilitator verify error: {e}")
        if getattr(result, "verified", False):
            return {
                "verified": True,
                "reason": "",
                "settlement": None,
                "facilitator": "self-hosted",
                "signer": getattr(result, "signer", ""),
                "amount_atomic": getattr(result, "amount_atomic", 0),
                "network": getattr(result, "network", None) or network_id,
                "asset": getattr(result, "asset", None) or asset,
            }
        reason = getattr(result, "reason", "") or ""
        if reason == "replay" or "replay" in reason:
            return {
                "verified": False,
                "reason": "transaction already used (replay)",
                "settlement": None,
                "network": network_id,
                "http_status": REPLAY_HTTP_STATUS,
            }

        # EIP-7702 self-verify fallback for EVM chains (receipt + payTo check).
        if network_family(network_id) == "evm":
            eip = await _eip7702_verify(tx_hash, network_id, asset, amount_usd)
            if eip["verified"]:
                eip["facilitator"] = "self-hosted-eip7702"
                eip["network"] = network_id
                eip["hex_reason"] = reason
                return eip
            reason = f"{reason}; EIP-7702: {eip.get('reason', '')}"
        elif network_family(network_id) == "solana":
            return await verify_solana(payment_id, tx_hash, network_id, asset, amount_usd)

        return {
            "verified": False,
            "reason": reason,
            "settlement": None,
            "facilitator": "self-hosted",
            "network": network_id,
        }

    def _scaffold_non_evm_verify(
        self,
        payment_id: str,
        tx_hash: str,
        network: str,
        asset: str,
        amount_usd: float,
    ) -> dict[str, Any]:
        """[REVIEW] Scaffolded acceptance for TRON — NEVER returns verified.

        Header structure, asset membership and non-negative USD amount are
        validated; on-chain settlement is NOT checked. Result is explicitly
        marked ``scaffolded`` and ``verified: false`` so it can never be
        treated as paid until a real TRC-20 verifier is wired in.
        """
        network_id = normalize_network_id(network)
        entry = resolve_asset_entry(network_id, asset)
        if not payment_id or not tx_hash:
            return {
                "verified": False,
                "reason": "needs_facilitator: missing payment_id/tx_hash in header",
                "settlement": None,
                "scaffolded": True,
                "network": network_id,
            }
        if entry is None:
            return {
                "verified": False,
                "reason": f"needs_facilitator: asset {asset!r} not in SUPPORTED_ASSETS "
                f"for {network_id}",
                "settlement": None,
                "scaffolded": True,
                "network": network_id,
            }
        if amount_usd is None or amount_usd < 0:
            return {
                "verified": False,
                "reason": "needs_facilitator: invalid (non-finite/negative) amount",
                "settlement": None,
                "scaffolded": True,
                "network": network_id,
            }
        return {
            "verified": False,
            "reason": "needs_facilitator: on-chain settlement verification "
            "not implemented for this chain",
            "settlement": None,
            "scaffolded": True,
            "network": network_id,
            "asset": asset,
            "supported": True,
            "verification_status": network_verification_status(network_id),
            "note": "Header structure, asset and price validated; settlement NOT verified. "
            "See VERIFICATION_STATUS.",
        }

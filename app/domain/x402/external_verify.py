# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Rug Munch Media LLC
# --- external_verify.py: external-facilitator verify + self-verify dispatch ---
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from .evm_verify import _eip7702_verify
from .external_verify_solana import verify_solana
from .external_verify_tron import verify_tron_fallback
from .facilitator_config import X402_NETWORK_WALLETS, X402_TIMEOUT_SECONDS, X402_WALLET
from .pricing import asset_to_atomic, network_family, normalize_network_id
from .replay import REPLAY_HTTP_STATUS
from .settlement_normalize import normalize_settlement

__all__ = ["verify_external", "verify_solana", "normalize_settlement"]


def _verify_payload(
    payment_id: str,
    tx_hash: str,
    network_id: str,
    asset: str,
    amount_usd: float,
) -> dict[str, Any]:
    """Exact-scheme verify request body sent to every healthy facilitator."""
    return {
        "paymentId": payment_id,
        "transactionHash": tx_hash,
        "network": network_id,
        "asset": asset,
        "amount": str(asset_to_atomic(amount_usd, asset, network_id)),
        "payToAddress": X402_NETWORK_WALLETS.get(network_id) or X402_WALLET,
        "scheme": "exact",
        "timestamp": datetime.now(UTC).isoformat(),
    }


async def _verify_via_facilitators(
    self: Any,
    payload: dict[str, Any],
    network_id: str,
) -> tuple[dict[str, Any] | None, str]:
    """Ask each healthy facilitator once; a 2xx body decides the result.

    Returns (result, last_error); result is not None the first time a
    facilitator answers with an HTTP 2xx body, None only when none was
    healthy or every attempt failed at the transport layer.
    """
    last_error = "No facilitators available"
    async with httpx.AsyncClient(timeout=X402_TIMEOUT_SECONDS) as client:
        for config in self.facilitators:
            if not self._is_healthy(config):
                continue
            try:
                response = await client.post(config.verify_url, json=payload)
                response.raise_for_status()
                data = response.json()
                self._mark_success(config)
                return _verified_body(data, config, network_id), ""
            except httpx.HTTPStatusError as e:
                last_error = f"{config.name}: HTTP {e.response.status_code}"
                self._mark_failure(config)
            except httpx.RequestError as e:
                last_error = f"{config.name}: {e}"
                self._mark_failure(config)
            except json.JSONDecodeError as e:
                last_error = f"{config.name}: invalid JSON - {e}"
                self._mark_failure(config)
    return None, last_error


def _verified_body(data: dict[str, Any], config: Any, network_id: str) -> dict[str, Any]:
    """Turn a facilitator verify body into the verdict dict (H3 honesty).

    A bare ``success``: true is NOT proof of payment -> require an
    explicit verification field (isValid/verified).
    """
    verified = bool(data.get("isValid") or data.get("verified"))
    if verified:
        ok, reason = (
            True,
            "",
        )  # single-use guard runs once in FacilitatorVerifyMixin.verify
        if not ok:
            return {
                "verified": False,
                "reason": reason,
                "settlement": None,
                "network": network_id,
                "http_status": REPLAY_HTTP_STATUS,
            }
    return {
        "verified": verified,
        "reason": data.get("error", data.get("reason", "")) if not verified else "",
        "settlement": data if verified else None,
        "facilitator": config.name,
        "network": network_id,
    }


@dataclass(frozen=True, slots=True)
class SelfVerifyRequest:
    """What the self-verify fallbacks need once facilitator verify failed."""

    network_id: str
    payment_id: str
    tx_hash: str
    asset: str
    amount_usd: float
    last_error: str


async def _self_verify_fallback(
    self: Any,
    req: SelfVerifyRequest,
) -> dict[str, Any]:
    """Self-verify fallbacks (EIP-7702 / Solana SPL / TRC-20) when external fails."""
    family = network_family(req.network_id)
    if family == "evm":
        eip = await _eip7702_verify(req.tx_hash, req.network_id, req.asset, req.amount_usd)
        if eip["verified"]:
            eip["facilitator"] = "self-hosted-eip7702"
            eip["network"] = req.network_id
            await self._enforce_replay(req.tx_hash, req.network_id)
            return eip
        last_error = eip.get("reason", req.last_error)
    elif family == "solana":
        # NOTE: module-level verify_solana, not a router method — the router
        # carries no _verify_solana (it only exists on the vendored reference
        # class), so self._verify_solana here would raise AttributeError.
        res = await verify_solana(
            req.payment_id, req.tx_hash, req.network_id, req.asset, req.amount_usd
        )
        if res.get("verified"):
            await self._enforce_replay(req.tx_hash, req.network_id)
        return res
    elif family == "tron":
        return await verify_tron_fallback(
            req.tx_hash, req.network_id, req.asset, req.amount_usd, self._enforce_replay
        )
    return {
        "verified": False,
        "reason": last_error,
        "settlement": None,
        "network": req.network_id,
    }


async def verify_external(
    self: Any,
    payment_id: str,
    tx_hash: str,
    network: str,
    asset: str,
    amount_usd: float,
) -> dict[str, Any]:
    """Verify a payment externally, then by self-hosted fallbacks, fail closed."""
    network_id = normalize_network_id(network)
    payload = _verify_payload(payment_id, tx_hash, network_id, asset, amount_usd)
    result, last_error = await _verify_via_facilitators(self, payload, network_id)
    if result is not None:
        return result
    req = SelfVerifyRequest(
        network_id=network_id,
        payment_id=payment_id,
        tx_hash=tx_hash,
        asset=asset,
        amount_usd=amount_usd,
        last_error=last_error,
    )
    return await _self_verify_fallback(self, req)

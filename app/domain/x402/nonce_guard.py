# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Rug Munch Media LLC
"""EIP-3009 authorization guard: validity window + single-use nonce.

Spec (``exact`` scheme): an authorization is single-use keyed on
``(network, asset, from, nonce)`` and valid only while
``validAfter <= now < validBefore``. The tx_hash key is kept as a SECOND
guard so a settled transaction can never fund a second call.
"""
from __future__ import annotations

import time
from typing import Any

from .pricing import normalize_network_id

__all__ = ["check_authorization", "nonce_replay_key", "tx_replay_key", "validity_error"]


def _field(authorization: dict[str, Any], *names: str) -> str:
    lowered = {str(k).lower(): v for k, v in (authorization or {}).items()}
    for name in names:
        value = lowered.get(name)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def validity_error(authorization: dict[str, Any], now: int | None = None) -> str:
    """Reason string when the EIP-3009 validity window is invalid, else ''."""
    if not authorization:
        return ""
    ts = int(time.time() if now is None else now)
    try:
        after = _field(authorization, "validafter", "valid_after")
        before = _field(authorization, "validbefore", "valid_before")
        if after and ts < int(after):
            return "authorization not yet valid (validAfter)"
        if before and ts >= int(before):
            return "authorization expired (validBefore)"
    except (TypeError, ValueError):
        return "authorization validity window malformed"
    return ""


def nonce_replay_key(network: str, asset: str, payer: str, nonce: str) -> str:
    """Canonical single-use key: CAIP-2 network + asset + payer + nonce."""
    return (
        f"x402:nonce:{normalize_network_id(network)}:{asset.strip().lower()}:"
        f"{payer.strip().lower()}:{nonce.strip().lower()}"
    )


def tx_replay_key(network: str, tx_hash: str) -> str:
    """Second guard: the settled transaction hash."""
    return f"x402:replay:{normalize_network_id(network)}:{str(tx_hash).strip().lower()}"


async def check_authorization(
    store: Any,
    authorization: dict[str, Any] | None,
    *,
    tx_hash: str,
    network: str,
    asset: str,
    now: int | None = None,
) -> tuple[bool, str]:
    """Consume the authorization nonce and the tx_hash. (ok, reason)."""
    auth = authorization or {}
    reason = validity_error(auth, now)
    if reason:
        return False, reason
    nonce = _field(auth, "nonce")
    payer = _field(auth, "from", "payer", "signer")
    if nonce and payer:
        key = nonce_replay_key(network, _field(auth, "asset") or asset, payer, nonce)
        if not await store.check_and_burn(key):
            return False, "authorization nonce already used (replay)"
    if tx_hash and not await store.check_and_burn(tx_replay_key(network, tx_hash)):
        return False, "transaction already used (replay)"
    return True, ""

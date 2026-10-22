"""Pay-per-call settlement: validate a proof, then verify + settle via Coffer.

Split out of ``gate`` to keep each module under the 200-line cap and to isolate
the money path — this is the only module that can move funds.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from store import coffer
from store.catalog import ToolSpec
from store.challenge import build_challenge
from store.config import Settings
from store.store_db import StoreDB


@dataclass(slots=True)
class Decision:
    """Outcome of one authorisation attempt."""

    allowed: bool
    status: int
    reason: str = ""
    receipt: dict[str, Any] | None = None
    challenge: dict[str, Any] | None = None


def today() -> str:
    """UTC day key for counters."""
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%d")


def _reject(tool: ToolSpec, settings: Settings, code: str, detail: str) -> Decision:
    return Decision(
        False, 402, code, challenge=build_challenge(tool, settings, error=code, detail=detail)
    )


def validate_proof(
    tool: ToolSpec, payment: dict[str, Any], settings: Settings
) -> tuple[str, str, float, str]:
    """Extract (payment_id, tx_hash, amount_usd, network) or raise ValueError."""
    payment_id = str(payment.get("paymentId") or payment.get("payment_id") or "").strip()
    tx_hash = str(payment.get("txHash") or payment.get("tx_hash") or "").strip()
    try:
        amount = float(payment.get("amountUsd") or payment.get("amount_usd") or 0)
    except (TypeError, ValueError):
        amount = 0.0
    network = str(payment.get("network") or settings.network).strip()
    if not payment_id or len(payment_id) > 128:
        raise ValueError("missing or oversized paymentId")
    if not tx_hash or len(tx_hash) > 128:
        raise ValueError("missing or oversized txHash")
    if amount + 1e-9 < tool.price_usd:
        raise ValueError(
            f"under-payment: paid {amount:.6f} USD, tool requires {tool.price_usd:.6f} USD"
        )
    if network not in {settings.network, settings.testnet_network}:
        raise ValueError(f"unsupported network {network}")
    return payment_id, tx_hash, amount, network


async def settle_payment(
    tool: ToolSpec,
    payment: dict[str, Any],
    db: StoreDB,
    settings: Settings,
    identity: str,
) -> Decision:
    """Run verify -> settle for one pay-per-call request."""
    try:
        payment_id, tx_hash, amount, network = validate_proof(tool, payment, settings)
    except ValueError as exc:
        return _reject(tool, settings, "invalid_payment_proof", str(exc))

    pid_key = f"payment:{payment_id}"
    if not db.claim(pid_key):
        return Decision(False, 409, "payment_already_used", receipt={"payment_id": payment_id})

    # Coffer reconciles on the asset SYMBOL it invoices (contract lives in extra).
    body = coffer.payment_body(payment_id, tx_hash, network, settings.asset_symbol, amount)
    verified = await coffer.verify(settings.coffer_url, body)
    if not verified.get("ok"):
        db.release(pid_key)  # a failed proof may be retried
        return _reject(
            tool,
            settings,
            "payment_verification_failed",
            str(verified.get("reason") or "unverified"),
        )

    tx_key = f"tx:{tx_hash.lower()}"
    if not db.claim(tx_key):
        db.release(pid_key)
        return Decision(
            False,
            409,
            "payment_already_used",
            receipt={"payment_id": payment_id, "tx_hash": tx_hash},
        )

    settled = await coffer.settle(settings.coffer_url, body)
    if not settled.get("ok"):
        db.release(pid_key)
        db.release(tx_key)
        return _reject(
            tool, settings, "settlement_failed", str(settled.get("reason") or "unsettled")
        )

    final_tx = settled.get("tx_hash") or tx_hash
    db.record_receipt(
        payment_id=payment_id,
        tool=tool.name,
        identity=identity,
        amount_usd=amount,
        tx_hash=final_tx,
        network=network,
    )
    db.add_spend(identity, today(), int(round(amount * 1_000_000)))
    return Decision(
        True,
        200,
        receipt={
            "mode": "x402",
            "payment_id": payment_id,
            "tx_hash": final_tx,
            "network": network,
            "amount_usd": amount,
            "facilitator": "coffer",
        },
    )

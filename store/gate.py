"""The gate — decides whether one tool call may run, and what it costs.

Order of precedence (first match wins):
  1. ``X-PAYMENT`` present  -> verify+settle with Coffer, then serve (pay-per-call)
  2. ``X-API-Key`` present  -> debit prepaid balance, then serve (prepaid)
  3. free tier allowance    -> serve without payment (server-side counter)
  4. otherwise              -> 402 + challenge

A paid call NEVER runs without settlement: settlement lives in
``store.payments`` and any failure there returns 402/409 with no payload.
"""

from __future__ import annotations

from typing import Any

from store import apikeys
from store.catalog import COFFER_PRICE_FLOOR_USD, ToolSpec
from store.challenge import build_challenge, decode_payment
from store.config import Settings
from store.payments import Decision, settle_payment, today
from store.store_db import StoreDB

PAYMENT_HEADER = "X-PAYMENT"
APIKEY_HEADER = "X-API-Key"
WALLET_HEADER = "X-WALLET"

__all__ = ["Decision", "authorize", "header", "resolve_identity"]


def header(headers: Any, name: str) -> str:
    """Case-insensitive header read (Starlette lowercases names; dicts may not)."""
    if headers is None:
        return ""
    value = headers.get(name)
    if value is None:
        value = headers.get(name.lower())
    if value is None:
        lowered = name.lower()
        for key, candidate in getattr(headers, "items", lambda: [])():
            if str(key).lower() == lowered:
                value = candidate
                break
    return str(value or "").strip()


def resolve_identity(headers: dict[str, str], client_ip: str, payment: dict[str, Any]) -> str:
    """Best payer identity: wallet > api-key hash > ip."""
    wallet = (header(headers, WALLET_HEADER) or payment.get("payer") or "").strip()
    if wallet:
        return f"wallet:{wallet.lower()}"
    api_key = header(headers, APIKEY_HEADER)
    if api_key:
        return f"key:{apikeys.hash_key(api_key)[:16]}"
    return f"ip:{client_ip or 'unknown'}"


async def authorize(
    tool: ToolSpec,
    headers: dict[str, str],
    client_ip: str,
    db: StoreDB,
    settings: Settings,
) -> Decision:
    """Authorise one tool call (see module docstring for precedence)."""
    payment = decode_payment(header(headers, PAYMENT_HEADER))
    identity = resolve_identity(headers, client_ip, payment)
    day = today()

    # Per-payer daily budget applies to every paid path.
    spent = db.spend_today(identity, day)
    if spent >= int(settings.daily_budget_usd * 1_000_000):
        return Decision(
            False,
            429,
            "daily_budget_exceeded",
            receipt={"identity": identity, "spentUsd": spent / 1_000_000},
        )

    if header(headers, PAYMENT_HEADER):
        return await settle_payment(tool, payment, db, settings, identity)

    api_key = header(headers, APIKEY_HEADER)
    if api_key and apikeys.looks_like_key(api_key):
        key_hash = apikeys.hash_key(api_key)
        balance = db.key_balance(key_hash)
        if balance is None:
            return Decision(False, 401, "invalid_api_key")
        micros = int(round(tool.price_usd * 1_000_000))
        if balance < micros:
            return Decision(
                False,
                402,
                "insufficient_prepaid_balance",
                receipt={"balanceUsd": balance / 1_000_000},
                challenge=build_challenge(
                    tool,
                    settings,
                    error="insufficient_prepaid_balance",
                    detail="top up or pay per call with x402",
                ),
            )
        if not db.debit_key(key_hash, micros):
            return Decision(False, 402, "insufficient_prepaid_balance")
        db.record_receipt(
            payment_id=f"key:{key_hash[:12]}:{day}:{spent}",
            tool=tool.name,
            identity=identity,
            amount_usd=tool.price_usd,
            tx_hash="prepaid",
            network="prepaid",
        )
        db.add_spend(identity, day, micros)
        return Decision(
            True,
            200,
            receipt={
                "mode": "prepaid",
                "amount_usd": tool.price_usd,
                "balanceUsd": (balance - micros) / 1_000_000,
            },
        )

    if settings.free_tier_enabled and tool.free_daily > 0:
        used = db.free_used(identity, tool.name, day)
        if used < tool.free_daily:
            db.consume_free(identity, tool.name, day)
            return Decision(
                True,
                200,
                receipt={
                    "mode": "free_tier",
                    "callsUsed": used + 1,
                    "callsLimit": tool.free_daily,
                    "amount_usd": 0.0,
                },
            )

    return Decision(
        False,
        402,
        "payment_required",
        challenge=build_challenge(
            tool,
            settings,
            error="payment_required",
            detail=f"free tier exhausted; {tool.name} costs ${tool.price_usd:.2f} "
            f"(Coffer floor ${COFFER_PRICE_FLOOR_USD:.2f})",
        ),
    )

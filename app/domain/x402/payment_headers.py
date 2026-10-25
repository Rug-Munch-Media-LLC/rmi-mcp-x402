# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Rug Munch Media LLC
"""Resolve the x402 payment proof from v1/v2 headers.

v1: ``X-PAYMENT`` (or ``X-Pay``, optional ``x402 `` prefix).
v2: ``PAYMENT-SIGNATURE`` (base64 JSON PaymentPayload).
``X-Payment-ID`` is an optional fleet shortcut: when present it wins and the
raw payload header is never required (never both).
"""
from __future__ import annotations

import base64
import binascii
import json
from typing import Any, Mapping

__all__ = ["decode_payment_payload", "derive_payment_id", "resolve_payment_proof"]

V2_HEADER = "PAYMENT-SIGNATURE"
V1_HEADERS = ("x-payment", "x-pay")


def decode_payment_payload(raw: str) -> dict[str, Any]:
    """Decode a base64 / plain-JSON / ``x402 <json>`` payment header."""
    text = (raw or "").strip()
    if not text:
        return {}
    if text.lower().startswith("x402 "):
        text = text[5:].strip()
    if text.startswith("{"):
        try:
            return json.loads(text)
        except ValueError:
            return {}
    try:
        decoded = base64.b64decode(text, validate=False).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return {}
    try:
        return json.loads(decoded) if decoded.lstrip().startswith("{") else {}
    except ValueError:
        return {}


def derive_payment_id(payload: Mapping[str, Any] | None) -> str:
    """Stable id from the authorization nonce: ``auth:<network>:<from>:<nonce>``."""
    body = dict(payload or {})
    auth = dict(body.get("payload") or {}).get("authorization") or body.get("authorization") or {}
    lowered = {str(k).lower(): v for k, v in dict(auth).items()}
    nonce = str(lowered.get("nonce") or "").strip().lower()
    if not nonce:
        return ""
    payer = str(lowered.get("from") or lowered.get("payer") or "").strip().lower()
    network = str(body.get("network") or (body.get("accepted") or {}).get("network") or "").lower()
    return f"auth:{network or 'unknown'}:{payer}:{nonce}"


def resolve_payment_proof(headers: Mapping[str, str]) -> dict[str, Any]:
    """Return ``{payment_id, header, version, payload}`` from either header."""
    lowered = {str(k).lower(): str(v) for k, v in dict(headers).items()}
    explicit = lowered.get("x-payment-id", "").strip()
    header, version, payload = "", 0, {}
    if lowered.get(V2_HEADER.lower(), "").strip():
        header, version = V2_HEADER, 2
        payload = decode_payment_payload(lowered[V2_HEADER.lower()])
    else:
        for name in V1_HEADERS:
            if lowered.get(name, "").strip():
                header, version = name.upper(), 1
                payload = decode_payment_payload(lowered[name])
                break
    return {
        "payment_id": explicit or derive_payment_id(payload),
        "header": header,
        "version": version,
        "payload": payload,
    }

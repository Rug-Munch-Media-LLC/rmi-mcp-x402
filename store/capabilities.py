"""Capability adapters — each store tool maps to a REAL, verified backend call.

Measured live against rmi-backend on :8000 (2026-10-23). No capability here
invents data: every endpoint below returned real payloads during the build,
and the exact endpoint is recorded so a future drift is a one-line diff.

Deliberately NOT used: `/api/v2/scanner/token/full/*` and `/risk/*` returned
`{"data": null}`, and `/api/v1/news/search` returned `[]` for every query —
mapping a paid tool onto those would sell an empty result.
"""

from __future__ import annotations

from typing import Any

import httpx

#: Fields that leak provider architecture; stripped from paid responses.
_DROP = frozenset(
    {"source", "is_local", "tier", "fallback_used", "consumer", "packaging", "cached"}
)


def _clean(value: Any) -> Any:
    """Recursively strip provider-identifying keys."""
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items() if k not in _DROP}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def _address_args(args: dict[str, Any]) -> tuple[str, str]:
    """Validate (chain, address) out of tool arguments."""
    address = str(args.get("address") or "").strip()
    chain = str(args.get("chain") or "base").strip().lower()
    if not (address.startswith("0x") and len(address) == 42):
        raise ValueError("address must be a 0x-prefixed 40-hex-character EVM address")
    if not chain.isalnum():
        raise ValueError("chain must be a simple slug such as 'base'")
    return chain, address


async def call_capability(
    capability: str,
    args: dict[str, Any],
    *,
    backend_url: str,
    timeout: float,
) -> dict[str, Any]:
    """Execute one capability against the live backend and return its payload."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        if capability == "scanner.token.scan":
            chain, address = _address_args(args)
            r = await client.post(
                f"{backend_url}/api/v2/scanner/token/scan",
                json={"chain": chain, "address": address},
            )
            r.raise_for_status()
            body = r.json()
            return {
                "verdict": body.get("risk_level"),
                "risk_score": body.get("safety_score"),
                "risk_level": body.get("risk_level"),
                "confidence": body.get("confidence"),
                "flags": body.get("risk_flags") or body.get("flags") or [],
                "chain": chain,
                "address": address,
                "raw": _clean(body),
            }

        if capability == "token.detail":
            chain, address = _address_args(args)
            r = await client.get(f"{backend_url}/api/v1/token/{chain}/{address}")
            r.raise_for_status()
            body = r.json()
            return {
                "token": _clean(body.get("token") or body),
                "security": _clean(body.get("security") or {}),
                "liquidity": _clean(body.get("dex_data") or body.get("liquidity") or {}),
                "chain": chain,
                "address": address,
            }

        if capability == "wallet.security":
            chain, address = _address_args(args)
            score = await client.get(f"{backend_url}/api/v1/scoring/wallet/{chain}/{address}")
            score.raise_for_status()
            labels: dict[str, Any] = {}
            try:
                lab = await client.post(
                    f"{backend_url}/api/v1/x402-databus/wallet-labels",
                    json={"chain": chain, "address": address},
                )
                if lab.status_code == 200:
                    labels = _clean(lab.json())
            except httpx.HTTPError:
                labels = {"error": "labels_unavailable"}
            body = score.json()
            return {
                "risk_level": "scammer" if body.get("is_scammer") else "clean",
                "is_scammer": bool(body.get("is_scammer")),
                "is_good_actor": bool(body.get("is_good_actor")),
                "labels": body.get("labels") or labels.get("data", {}).get("labels", []),
                "deployer_profile": _clean(body.get("deployer_profile")),
                "chain": chain,
                "address": address,
                "label_source": labels.get("data", {}),
            }

        if capability == "news.headlines":
            query = str(args.get("query") or "").strip()
            limit = min(int(args.get("limit") or 10), 50)
            r = await client.get(
                f"{backend_url}/api/v1/news/headlines",
                params={"limit": max(limit * 4, 20)},
            )
            r.raise_for_status()
            headlines = _clean(r.json().get("headlines") or [])
            needle = query.lower()
            matched = (
                [h for h in headlines if needle in str(h.get("title", "")).lower()]
                if needle
                else []
            )
            return {
                "query": query,
                "matched": bool(matched),
                "articles": (matched or headlines)[:limit],
                "note": None if matched else "no keyword match; returning freshest headlines",
            }

        if capability == "rag.search":
            query = str(args.get("query") or "").strip()
            if not query:
                raise ValueError("query is required")
            limit = min(int(args.get("limit") or 5), 20)
            r = await client.post(
                f"{backend_url}/api/v1/rag/v2/search",
                json={"query": query, "limit": limit, "collections": ["scam_intel"]},
            )
            r.raise_for_status()
            return _clean(r.json())

    raise ValueError(f"unknown capability: {capability}")

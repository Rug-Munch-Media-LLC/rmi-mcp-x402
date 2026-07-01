"""Add is_verified badge status to scan responses.

This middleware-style helper enriches scan responses with verification
status from the Rug Munch Verified badge system. AI agents and the
web UI can use this to show the Rug Munch Verified badge on results.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.logging import get_logger

log = get_logger(__name__)

# Endpoint to check verification status
VERIFIED_CHECK_URL = "/api/v1/verified/mcp/check/{chain}/{address}"


async def enrich_with_verified_badge(
    response_data: dict[str, Any], chain: str, address: str
) -> dict[str, Any]:
    """Enrich a scan/wallet response with is_verified badge status.

    Adds an is_verified field to the response so MCP tools and the web
    UI can show the Rug Munch Verified badge alongside scan results.

    Args:
        response_data: The scan or wallet response data.
        chain: Blockchain (ethereum, solana, base, etc.)
        address: Token or wallet address.

    Returns:
        Same response_data with is_verified, badge_status, and
        badge_url fields added.
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                VERIFIED_CHECK_URL.format(chain=chain, address=address)
            )
            if resp.status_code == 200:
                badge = resp.json()
                response_data["is_verified"] = badge.get("is_verified", False)
                response_data["badge_status"] = badge.get("status", "none")
                response_data["badge_verified_at"] = badge.get("verified_at")
                response_data["badge_expires_at"] = badge.get("badge_expires_at")
                response_data["badge_url"] = badge.get("public_url")
    except Exception as e:
        log.debug("verified_badge_enrichment_failed chain=%s err=%s", chain, e)
        # Don't fail the whole request if badge check fails
        response_data.setdefault("is_verified", False)
        response_data.setdefault("badge_status", "unknown")

    return response_data

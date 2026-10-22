"""Catalog rendering — the machine-readable view served to bots and the website.

Split from ``catalog`` (data) so each module stays under the 200-line cap.
"""

from __future__ import annotations

from typing import Any

from store.catalog import COFFER_PRICE_FLOOR_USD, TOOLS


def catalog_payload() -> dict[str, Any]:
    """Machine-readable catalog for bots and the website."""
    return {
        "store": "Rug Munch Intelligence",
        "version": "1.0.0",
        "protocol": "x402",
        "x402Version": 1,
        "facilitator": "coffer",
        "pricingNote": (
            f"Prices are floored at Coffer's authorised api_access price "
            f"(${COFFER_PRICE_FLOOR_USD:.2f}); intendedUsd records the marketing target."
        ),
        "tools": [
            {
                "name": t.name,
                "title": t.title,
                "description": t.description,
                "priceUsd": t.price_usd,
                "priceAtoms": str(t.price_atoms),
                "intendedUsd": t.intended_usd,
                "freeTier": {"callsPerDay": t.free_daily},
                "tier": t.tier,
                "inputSchema": t.input_schema,
                "outputSchema": t.output_schema,
                "exampleCall": {"tool": t.name, "arguments": t.example},
            }
            for t in TOOLS
        ],
    }

#!/usr/bin/env python3
"""
RugMunch Data MCP — Pry web scraping tool (x402-gated data layer).
"""

from typing import Optional

import httpx

from data_mcp_config import PRY_API_URL
from data_mcp_server import mcp


@mcp.tool()
async def web_scrape(
    url: str,
    formats: Optional[list[str]] = None,
    bypass_cloudflare: bool = True,
    js_render: bool = True,
) -> dict:
    """Scrape any webpage via the self-hosted Pry engine and return clean,
    LLM-ready markdown or JSON. Adaptive 12-tier anti-bot escalation handles
    Cloudflare/DataDome/Akamai automatically when bypass_cloudflare is set.

    Args:
        url: Absolute http(s) URL of the page to scrape.
        formats: Output formats, e.g. ["markdown"] or ["json"]. Defaults to markdown.
        bypass_cloudflare: Enable adaptive anti-bot escalation (default true).
        js_render: Allow browser-tier rendering for JS-heavy pages (default true).

    Returns:
        {"url": ..., "status": "ok"|"error", "content": <markdown/json>, "method": <tier used>}
    """
    payload: dict = {
        "urls": [url],
        "output_format": (formats or ["markdown"])[0],
    }
    if not bypass_cloudflare:
        payload["max_tier"] = "direct"
    if not js_render:
        payload["disable_browser"] = True
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(f"{PRY_API_URL}/v1/scrape", json=payload)
        resp.raise_for_status()
        return resp.json()

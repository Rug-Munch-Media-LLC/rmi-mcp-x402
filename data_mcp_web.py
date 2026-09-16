#!/usr/bin/env python3
"""
RugMunch Data MCP — Pry web scraping tool (x402-gated data layer).
"""

from typing import Any, Optional

import httpx

from data_mcp_config import PRY_API_KEY, PRY_API_URL
from data_mcp_server import mcp


async def _scrape(
    url: str,
    formats: Optional[list[str]] = None,
    bypass_cloudflare: bool = True,
    js_render: bool = True,
    *,
    client: Any = None,
    api_key: Optional[str] = None,
) -> dict:
    """Scrape one URL via the Pry API. Dependency-injected for tests.

    Args:
        client: httpx.AsyncClient-compatible object; None creates a default
            client. Tests pass a fake — no patching required.
        api_key: Bearer credential override; None uses the module default
            (from the fleet secrets env), "" forces anonymous.

    Returns:
        {"url": ..., "status": "ok"|"error", "content": <markdown/json>,
         "method": <tier used>}
    """
    # Payload matches the Pry API `ScrapeRequest` model (camelCase fields).
    payload: dict = {
        "url": url,
        "formats": formats or ["markdown"],
        "onlyMainContent": True,
        "bypassCloudflare": bool(bypass_cloudflare),
        "jsRender": bool(js_render),
    }
    # Credentialed requests bypass the x402 payment gate: the gate monetizes
    # KEYLESS external agent traffic; internal use sends the operator key.
    key = PRY_API_KEY if api_key is None else api_key
    headers = {"Authorization": f"Bearer {key}"} if key else {}

    async def _post(http_client: Any) -> dict:
        resp = await http_client.post(f"{PRY_API_URL}/v1/scrape", json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    if client is not None:
        body = await _post(client)
    else:
        async with httpx.AsyncClient(timeout=120) as owned:
            body = await _post(owned)

    if isinstance(body, dict) and body.get("success") and isinstance(body.get("data"), dict):
        data = body["data"]
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        return {
            "url": url,
            "status": "ok",
            "content": data.get("markdown") or data,
            "method": metadata.get("method") or metadata.get("tier") or metadata.get("strategy"),
            "metadata": metadata,
        }
    return {"url": url, "status": "error", "error": body}


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
    return await _scrape(url, formats, bypass_cloudflare, js_render)

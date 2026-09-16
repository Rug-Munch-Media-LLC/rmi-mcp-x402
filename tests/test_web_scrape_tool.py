"""Tests for the web_scrape MCP tool (Pry engine bridge).

Dependency-injected: fakes are passed in via ``_scrape(client=...)`` /
``_scrape(api_key=...)`` — no attribute patching anywhere (fleet policy:
NEVER monkeypatch; the structure gate enforces this).
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest


@pytest.fixture
def web():
    return importlib.import_module("data_mcp_web")


class FakeResp:
    def __init__(self, body: dict) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._body


class FakeClient:
    """httpx.AsyncClient-compatible test double (injected, never patched)."""

    def __init__(self, body: dict) -> None:
        self.body = body
        self.calls: list[dict[str, Any]] = []

    async def post(self, url: str, json: dict, headers: dict | None = None) -> FakeResp:
        self.calls.append({"url": url, "json": json, "headers": headers})
        return FakeResp(self.body)


class TestWebScrapeTool:
    def test_tool_registered(self) -> None:
        mod = importlib.import_module("data_mcp")
        assert hasattr(mod, "web_scrape")

    @pytest.mark.asyncio
    async def test_payload_matches_pry_scrape_request_schema(self, web: Any) -> None:
        client = FakeClient(
            {"success": True, "data": {"markdown": "# hi", "metadata": {"method": "direct"}}}
        )
        result = await web._scrape("https://example.com", client=client)

        call = client.calls[0]
        assert call["url"].endswith("/v1/scrape")
        # Pry's ScrapeRequest: singular url, camelCase flags (was urls/output_format).
        assert call["json"] == {
            "url": "https://example.com",
            "formats": ["markdown"],
            "onlyMainContent": True,
            "bypassCloudflare": True,
            "jsRender": True,
        }
        assert result["status"] == "ok"
        assert result["content"] == "# hi"
        assert result["method"] == "direct"

    @pytest.mark.asyncio
    async def test_flags_disable_browser_and_bypass(self, web: Any) -> None:
        client = FakeClient({"success": True, "data": {"markdown": ""}})
        await web._scrape(
            "https://x", formats=["json"], bypass_cloudflare=False, js_render=False, client=client
        )
        assert client.calls[0]["json"]["formats"] == ["json"]
        assert client.calls[0]["json"]["bypassCloudflare"] is False
        assert client.calls[0]["json"]["jsRender"] is False

    @pytest.mark.asyncio
    async def test_bearer_header_sent_when_key_present(self, web: Any) -> None:
        client = FakeClient({"success": True, "data": {"markdown": "x"}})
        await web._scrape("https://example.com", client=client, api_key="test-key-123")
        # Credentialed traffic bypasses the x402 payment gate.
        assert client.calls[0]["headers"] == {"Authorization": "Bearer test-key-123"}

    @pytest.mark.asyncio
    async def test_no_auth_header_without_key(self, web: Any) -> None:
        client = FakeClient({"success": True, "data": {"markdown": "x"}})
        await web._scrape("https://example.com", client=client, api_key="")
        assert client.calls[0]["headers"] == {}

    @pytest.mark.asyncio
    async def test_error_body_maps_to_error_status(self, web: Any) -> None:
        client = FakeClient({"success": False, "error": "nope"})
        result = await web._scrape("https://example.com", client=client)
        assert result["status"] == "error"
        assert result["error"] == {"success": False, "error": "nope"}

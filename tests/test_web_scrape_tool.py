"""Tests for the web_scrape MCP tool (Pry engine bridge)."""

from __future__ import annotations

import importlib
from typing import Any

import pytest


@pytest.fixture
def mod():
    return importlib.import_module("data_mcp")


class TestWebScrapeTool:
    def test_tool_registered(self, mod: Any) -> None:
        assert hasattr(mod, "web_scrape")

    @pytest.mark.asyncio
    async def test_posts_to_pry_api_and_returns_payload(
        self, mod: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict = {}

        class FakeResp:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {"url": "https://x", "status": "ok", "content": "# hi", "method": "direct"}

        class FakeClient:
            def __init__(self, **kw: Any) -> None:
                pass

            async def __aenter__(self) -> "FakeClient":
                return self

            async def __aexit__(self, *exc: Any) -> None:
                return None

            async def post(self, url: str, json: dict) -> FakeResp:
                captured["url"] = url
                captured["json"] = json
                return FakeResp()

        monkeypatch.setattr(mod.httpx, "AsyncClient", FakeClient)
        result = await mod.web_scrape("https://example.com")
        assert result["status"] == "ok"
        assert captured["url"].endswith("/v1/scrape")
        assert captured["json"]["urls"] == ["https://example.com"]
        assert captured["json"]["output_format"] == "markdown"

    @pytest.mark.asyncio
    async def test_flags_disable_browser_tier(
        self, mod: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict = {}

        class FakeResp:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {"status": "ok"}

        class FakeClient:
            def __init__(self, **kw: Any) -> None:
                pass

            async def __aenter__(self) -> "FakeClient":
                return self

            async def __aexit__(self, *exc: Any) -> None:
                return None

            async def post(self, url: str, json: dict) -> FakeResp:
                captured["json"] = json
                return FakeResp()

        monkeypatch.setattr(mod.httpx, "AsyncClient", FakeClient)
        await mod.web_scrape("https://x", bypass_cloudflare=False, js_render=False)
        assert captured["json"]["max_tier"] == "direct"
        assert captured["json"]["disable_browser"] is True

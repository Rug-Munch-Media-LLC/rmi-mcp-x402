"""Catalog contract tests: pricing floor, schemas, machine-readable output."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from store_harness import store_app  # noqa: E402  # pyright: ignore[reportImplicitRelativeImport]

from store.catalog import COFFER_PRICE_FLOOR_USD, TOOLS, get_tool  # noqa: E402

EXPECTED = {"intel.scan", "scanner.token", "security.wallet", "news.search", "exploit.lookup"}


def test_every_tool_priced_at_or_above_coffer_floor():
    assert len(TOOLS) == 5
    for tool in TOOLS:
        assert tool.price_usd >= COFFER_PRICE_FLOOR_USD, tool.name
        assert tool.price_atoms >= 10_000, tool.name
        assert tool.free_daily >= 1, tool.name
        assert tool.input_schema["type"] == "object"
        assert tool.example, tool.name


def test_catalog_endpoint_is_machine_readable(tmp_path):
    with store_app(tmp_path) as h:
        body = h.client.get("/store/catalog").json()
    assert body["version"] == "1.0.0"
    assert body["payment"]["payTo"].startswith("0x")
    assert {t["name"] for t in body["tools"]} == EXPECTED
    for tool in body["tools"]:
        assert tool["exampleCall"]["tool"] == tool["name"]
        assert tool["freeTier"]["callsPerDay"] >= 1
        assert tool["inputSchema"]["properties"]
        assert tool["outputSchema"]["type"] == "object"


def test_well_known_points_at_catalog_and_mcp(tmp_path):
    with store_app(tmp_path) as h:
        body = h.client.get("/.well-known/x402").json()
    assert body["x402Version"] == 1
    assert body["mcp"]["endpoint"].endswith("/mcp")
    assert body["catalog"].endswith("/store/catalog")
    assert len(body["tools"]) == 5


def test_tools_list_carries_prices(tmp_path):
    with store_app(tmp_path) as h:
        body = h.client.post(
            "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        ).json()
    tools = {t["name"]: t for t in body["result"]["tools"]}
    assert set(tools) == EXPECTED
    for name, entry in tools.items():
        spec = get_tool(name)
        assert spec is not None
        assert entry["annotations"]["priceUsd"] == spec.price_usd
        assert entry["annotations"]["priceAtoms"] == str(spec.price_atoms)
        assert entry["annotations"]["freeTierCallsPerDay"] == spec.free_daily
        assert "Price: $" in entry["description"]


def test_healthz_reports_facilitator(tmp_path):
    with store_app(tmp_path) as h:
        body = h.client.get("/healthz").json()
    assert body["store"] == "rmi-intelligence"
    assert body["tools"] == 5
    assert body["facilitator"]["name"] == "coffer"

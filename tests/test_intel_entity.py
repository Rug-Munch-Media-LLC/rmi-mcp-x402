"""intel_entity_search: Postgres entity/label lookup, faked through IntelDeps."""

from __future__ import annotations

import pytest
from intel_harness import EVM_ADDR, TAG, FakePG, deps

import data_mcp_intel_entity as entity_tool


@pytest.mark.asyncio
async def test_entity_search_without_postgres_says_so() -> None:
    out = await entity_tool.search_entities("bad", deps(pg=None))
    assert out == {"ok": False, "error": entity_tool.NO_POSTGRES}


@pytest.mark.asyncio
async def test_entity_search_returns_matches() -> None:
    pg = FakePG(rows=[TAG])
    out = await entity_tool.search_entities("bad", deps(pg=pg))
    assert out["ok"] is True and out["matches"] == 1
    assert out["results"][0]["address"] == EVM_ADDR
    assert out["results"][0]["entity"] == "Bad Corp"
    assert pg.closed is True


@pytest.mark.asyncio
async def test_entity_search_returns_a_typed_failure() -> None:
    out = await entity_tool.search_entities("bad", deps(pg=FakePG(error=RuntimeError("boom"))))
    assert out["ok"] is False and "boom" in out["error"]

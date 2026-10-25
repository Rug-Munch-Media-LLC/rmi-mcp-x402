"""intel_duckdb_query and intel_archive_schema: the operator-facing archive tools."""

from __future__ import annotations

import pytest
from intel_harness import FakeDuck, deps

import data_mcp_intel_duck as duck_tool
import data_mcp_intel_schema as schema_tool


@pytest.mark.asyncio
async def test_duckdb_query_is_refused_without_a_token() -> None:
    assert await duck_tool.query_archives("SELECT 1", deps(token="")) == {
        "ok": False,
        "error": duck_tool.NO_TOKEN,
    }


@pytest.mark.asyncio
async def test_duckdb_query_truncates_at_the_row_cap() -> None:
    duck = FakeDuck(records=[{"n": i} for i in range(150)])
    out = await duck_tool.query_archives("SELECT * FROM t", deps(duck=duck, token="t"))
    assert out["rows"] == 150
    assert len(out["data"]) == duck_tool.MAX_ROWS
    assert out["truncated"] is True
    assert out["columns"] == ["n"]


@pytest.mark.asyncio
async def test_duckdb_query_reports_sql_errors() -> None:
    out = await duck_tool.query_archives(
        "SELECT bad", deps(duck=FakeDuck(error=ValueError("x")), token="t")
    )
    assert out["ok"] is False


@pytest.mark.asyncio
async def test_archive_schema_is_static_and_never_touches_the_network() -> None:
    out = await schema_tool.intel_archive_schema()
    assert out["ok"] is True
    assert set(out["chains"]) == {"solana", "evm"}
    assert out["storage"]["provider"] == "Hugging Face Hub"

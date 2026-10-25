"""Archive history: the DuckDB/HF-Hub source of the wallet profile.

The token gate and the DuckDB handle both arrive through `IntelDeps`, so the
archive path is exercised without credentials, files or network.
"""

from __future__ import annotations

import pytest
from intel_harness import EVM_ADDR, SOL_ADDR, TX, FakeDuck, deps

import data_mcp_intel_history as history


@pytest.mark.asyncio
async def test_history_is_skipped_without_a_token() -> None:
    duck = FakeDuck(records=[TX])
    assert await history.archive_history(SOL_ADDR, deps(duck=duck, token="")) == {}
    assert duck.statements == []  # no archive query was attempted


@pytest.mark.asyncio
async def test_history_summarises_matches() -> None:
    out = await history.archive_history(EVM_ADDR, deps(duck=FakeDuck(records=[TX]), token="tok"))
    assert out["total_matches"] == 1
    assert out["unique_counterparties"] == 1
    assert out["sample_tx"] == [TX]


@pytest.mark.asyncio
async def test_history_reports_an_empty_archive_as_empty() -> None:
    assert await history.archive_history(SOL_ADDR, deps(duck=FakeDuck(records=[]), token="t")) == {}


@pytest.mark.asyncio
async def test_history_reports_a_query_failure_with_a_hint() -> None:
    out = await history.archive_history(
        SOL_ADDR, deps(duck=FakeDuck(error=RuntimeError("no files")), token="t")
    )
    assert "no files" in out["error"]
    assert out["note"] == history.NO_ARCHIVES


@pytest.mark.asyncio
async def test_history_uses_the_solana_directory_for_a_solana_address() -> None:
    duck = FakeDuck(records=[TX])
    await history.archive_history(SOL_ADDR, deps(duck=duck, token="t"))
    assert "/solana/" in duck.statements[0][0]


@pytest.mark.xfail(
    strict=True,
    reason="pre-existing: chain_dir keys off len(address) <= 44, so every 0x EVM "
    "address (42 chars) is routed to the solana archive",
)
@pytest.mark.asyncio
async def test_history_routes_an_evm_address_to_the_evm_archives() -> None:
    """Known defect, pinned. Fixing the heuristic flips this to XPASS -> update it."""
    duck = FakeDuck(records=[TX])
    await history.archive_history(EVM_ADDR, deps(duck=duck, token="t"))
    assert "/evm/" in duck.statements[0][0]

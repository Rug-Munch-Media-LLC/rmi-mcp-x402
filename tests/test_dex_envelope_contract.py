"""The DEX envelope contract, proven per tool from recorded fixtures.

Three guarantees for every DexScreener/GeckoTerminal tool:

  1. Success returns exactly {data, source, fetched_at, stale, age_s} with
     `data` present (never None) and `stale` False — no empty success.
  2. A provider failure raises the typed `DataUnavailable` carrying source and
     status — never an empty envelope, never a bare exception.
  3. An upstream that legitimately returns zero rows is a success, not a
     failure.

Fixtures only: recorded payloads replayed with respx; no network, no patching.
Cache/stale behaviour: test_dex_cache_windows.py.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from dex_cases import CASES, ENVELOPE, FIX, Case

import data_mcp_dex_core as core


def fx(name: str) -> object:
    return json.loads((FIX / name).read_text())


@pytest.fixture(autouse=True)
def _clean() -> None:
    core.STORE.clear()
    for gate in core.GATES.values():
        gate.reset()


@pytest.fixture(params=CASES, ids=[c.name for c in CASES])
def case(request) -> Case:
    return request.param


@pytest.mark.asyncio
@respx.mock
async def test_tool_returns_the_envelope(case: Case) -> None:
    respx.get(url__startswith=case.prefix).mock(
        return_value=httpx.Response(200, json=fx(case.fixture))
    )
    env = await case.call()
    assert set(env) == ENVELOPE, case.name
    assert env["source"] == case.source
    assert env["stale"] is False
    assert env["data"] is not None, f"{case.name} returned an empty success"
    assert isinstance(env["fetched_at"], float) and env["fetched_at"] > 0
    assert env["age_s"] >= 0


@pytest.mark.asyncio
@respx.mock
async def test_tool_raises_typed_error_when_provider_is_down(case: Case) -> None:
    # 404 is non-retryable, so the failure surfaces on the first attempt.
    respx.get(url__startswith=case.prefix).mock(return_value=httpx.Response(404, json={}))
    with pytest.raises(core.DataUnavailable) as info:
        await case.call()
    assert info.value.source == case.source
    assert info.value.status == 404
    assert info.value.payload()["type"] == "DataUnavailable"


@pytest.mark.asyncio
@respx.mock
async def test_empty_upstream_payload_is_a_success(case: Case) -> None:
    """Zero rows is data; only a failed fetch may raise."""
    respx.get(url__startswith=case.prefix).mock(return_value=httpx.Response(200, json=case.empty))
    env = await case.call()
    assert env["stale"] is False
    assert env["data"] is not None

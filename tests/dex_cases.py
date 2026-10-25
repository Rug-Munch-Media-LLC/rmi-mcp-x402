"""The DEX tool table: one Case per tool, filled from recorded fixtures.

Every DEX tool is exercised the same way, so the table lives here and the two
contract suites (envelope, cache windows) parametrise over it.
"""

from __future__ import annotations

import pathlib
from typing import Callable

import data_mcp_dex as dex
import data_mcp_gecko as gecko

FIX = pathlib.Path(__file__).parent / "fixtures" / "dex"
DS = "https://api.dexscreener.com"
GT = "https://api.geckoterminal.com/api/v2"
SOL = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
EVM = "0xAbC0000000000000000000000000000000000001"
POOL = "7Yk7UAsViEvP5wkyfapAFS7KySmCnJtpvamxmCMKiavU"

ENVELOPE = {"data", "source", "fetched_at", "stale", "age_s"}

_FIXTURE = {
    "dex_search": "dexscreener_search.json",
    "dex_pairs": "dexscreener_pairs.json",
    "dex_token": "dexscreener_token.json",
    "dex_boosts": "dexscreener_boosts_latest.json",
    "dex_profiles": "dexscreener_profiles.json",
    "dex_pools": "geckoterminal_pools.json",
    "dex_ohlcv": "geckoterminal_ohlcv.json",
}

_SOURCE = {
    "dex_search": "dexscreener",
    "dex_pairs": "dexscreener",
    "dex_token": "dexscreener",
    "dex_boosts": "dexscreener",
    "dex_profiles": "dexscreener",
    "dex_pools": "geckoterminal",
    "dex_ohlcv": "geckoterminal",
}

# A schema-valid body that carries zero rows, per tool.
_EMPTY = {
    "dex_search": {"pairs": []},
    "dex_pairs": {"pairs": []},
    "dex_token": {"pairs": []},
    "dex_boosts": [],
    "dex_profiles": [],
    "dex_pools": {"data": [], "meta": {}},
    "dex_ohlcv": {"data": {"attributes": {"ohlcv_list": []}}, "meta": {}},
}


class Case:
    """One DEX tool: its fixture-backed call and the upstream URL prefix."""

    def __init__(self, name: str, call: Callable[[], object], prefix: str) -> None:
        self.name, self.call, self.prefix = name, call, prefix

    @property
    def fixture(self) -> str:
        return _FIXTURE[self.name]

    @property
    def source(self) -> str:
        return _SOURCE[self.name]

    @property
    def empty(self) -> object:
        return _EMPTY[self.name]


CASES = [
    Case("dex_search", lambda: dex.dex_search("bonk", limit=2), f"{DS}/latest/dex/search"),
    Case("dex_pairs", lambda: dex.dex_pairs("base", EVM), f"{DS}/latest/dex/pairs/base/"),
    Case("dex_token", lambda: dex.dex_token(SOL, chain="solana"), f"{DS}/latest/dex/tokens/"),
    Case("dex_boosts", lambda: dex.dex_boosts("latest", limit=2), f"{DS}/token-boosts/latest/v1"),
    Case("dex_profiles", lambda: dex.dex_profiles(limit=2), f"{DS}/token-profiles/latest/v1"),
    Case("dex_pools", lambda: gecko.dex_pools("solana", limit=2), f"{GT}/networks/solana/pools"),
    Case(
        "dex_ohlcv",
        lambda: gecko.dex_ohlcv("solana", POOL, resolution="hour", limit=5),
        f"{GT}/networks/solana/pools/{POOL}/ohlcv/hour",
    ),
]

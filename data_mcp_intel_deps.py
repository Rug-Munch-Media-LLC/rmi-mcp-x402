#!/usr/bin/env python3
"""
RugMunch Data MCP — dependency seam for the intelligence tools.

One responsibility: carry the collaborators a wallet profile needs, so tests
pass fakes instead of patching module globals (fleet rule: NEVER monkeypatch —
use a dependency-injection seam). `hf_token` gates the archive queries; the
default is the environment value resolved at import.
"""

from dataclasses import dataclass
from typing import Any, Callable

from data_mcp_config import HF_HUB_TOKEN
from data_mcp_duck import _get_duckdb
from data_mcp_pg import _get_pg
from data_mcp_rpc import _rpc_call


@dataclass(frozen=True)
class IntelDeps:
    """Default wiring is production; tests override any field."""

    rpc_call: Callable[..., Any] = _rpc_call
    get_pg: Callable[..., Any] = _get_pg
    get_duckdb: Callable[..., Any] = _get_duckdb
    hf_token: str = HF_HUB_TOKEN

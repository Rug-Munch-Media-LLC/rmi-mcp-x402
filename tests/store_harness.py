"""Explicit test harness for the MCP store (no fixtures, no network egress).

Deliberately not a pytest fixture module: the repo's ``tests/conftest.py`` is
the existing Coffer end-to-end harness and must not be touched or shadowed.

DI seam: ``store.app`` and ``store.capabilities`` hold module attributes that
the production code resolves lazily at request time. Tests swap them for the
whole ``store_app(...)`` context and the originals are restored on exit —
explicit, observable, no monkeypatching.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from store import app as app_module  # noqa: E402
from store import capabilities as capabilities_module  # noqa: E402
from store.config import Settings  # noqa: E402
from store.store_db import StoreDB  # noqa: E402

TOOL = "intel.scan"
ARGS = {"chain": "base", "address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"}


def make_settings(tmp_path: Path) -> Settings:
    """Settings pointing at nothing real."""
    return Settings(
        backend_url="http://backend.test",
        coffer_url="http://coffer.test",
        pay_to="0x66Aa99d25CAD3c9680abc2636812c720329aA690",
        network="eip155:8453",
        asset_contract="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        asset_symbol="USDC",
        testnet_network="eip155:84532",
        testnet_asset="0x036CbD53842c5426634e7929541eC2318f3dCF7e",
        public_base_url="http://store.test",
        db_path=tmp_path / "store.db",
        daily_budget_usd=1.0,
        free_tier_enabled=True,
        timeout_seconds=5.0,
        admin_key="admin-secret",
    )


async def _default_capability(
    capability: str, args: dict[str, Any], *, backend_url: str, timeout: float
) -> dict[str, Any]:
    """Stub the backend capability call; answers offline."""
    return {"ok": True, "capability": capability, "chain": args.get("chain", "base")}


@dataclass(slots=True)
class Harness:
    """A wired store: settings, db and an HTTP client."""

    settings: Settings
    db: StoreDB
    client: Any


@dataclass(slots=True)
class Swap:
    """Swap one module attribute for a context; restore the original on exit."""

    module: object
    attr: str
    new: Any
    original: Any = None

    def __enter__(self) -> "Swap":
        self.original = getattr(self.module, self.attr)
        setattr(self.module, self.attr, self.new)
        return self

    def __exit__(self, *exc: object) -> None:
        setattr(self.module, self.attr, self.original)


@contextmanager
def store_app(
    tmp_path: Path,
    *,
    capability: Any = None,
) -> Iterator[Harness]:
    """Isolated store instance; capability layer stubbed unless overridden."""
    from fastapi.testclient import TestClient

    settings = make_settings(tmp_path)
    db = StoreDB(settings.db_path)
    with (
        Swap(app_module, "_settings", settings),
        Swap(app_module, "_db", db),
        Swap(capabilities_module, "call_capability", capability or _default_capability),
    ):
        yield Harness(settings=settings, db=db, client=TestClient(app_module.app))


def call(
    client: Any, name: str, arguments: dict[str, Any], headers: dict[str, str] | None = None
) -> Any:
    """Issue an MCP tools/call."""
    return client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers=headers or {},
    )

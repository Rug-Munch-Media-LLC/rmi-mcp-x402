"""Focused tests for the store test-harness DI seam (Swap + store_app).

The fleet anti-pattern gate bans intercepted test-patching; the harness
replaces it with explicit, self-restoring module-attribute swaps. These tests pin the
revert contract: after either seam closes, production module state is exactly
what it was before.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from store_harness import Swap, store_app  # noqa: E402  # pyright: ignore[reportImplicitRelativeImport]

from store import app as app_module  # noqa: E402
from store import coffer  # noqa: E402


async def _sentinel(base_url: str, body: dict[str, object]) -> dict[str, object]:
    return {"ok": False, "reason": "sentinel", "status": 200}


def test_swap_restores_module_attr(tmp_path):
    """Swapped module attr reverts to the original object after the context."""
    original = coffer.verify

    with store_app(tmp_path):
        with Swap(coffer, "verify", _sentinel):
            assert coffer.verify is _sentinel
        assert coffer.verify is original


def test_store_app_restores_settings_and_db(tmp_path):
    """App-level seams (settings, db) revert after ``store_app`` closes."""
    original_settings = app_module._settings
    original_db = app_module._db

    with store_app(tmp_path) as h:
        assert app_module._settings is h.settings
        assert app_module._db is h.db
    assert app_module._settings is original_settings
    assert app_module._db is original_db


def test_store_app_reads_isolated_settings(tmp_path):
    """The isolated store serves with its tmp-path settings, not the global."""
    with store_app(tmp_path) as h:
        body = h.client.get("/healthz").json()
        assert h.settings.public_base_url == "http://store.test"
        assert body["store"] == "rmi-intelligence"

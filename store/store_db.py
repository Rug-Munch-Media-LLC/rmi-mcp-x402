"""Durable store state: replay guard, free-tier counters, budgets, ledger.

SQLite (stdlib, WAL) so the store has no daemon dependency and a settled
payment survives a restart. Four tables, one responsibility each.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS consumed (
    key TEXT PRIMARY KEY,
    consumed_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS free_usage (
    identity TEXT NOT NULL,
    tool TEXT NOT NULL,
    day TEXT NOT NULL,
    used INTEGER NOT NULL,
    PRIMARY KEY (identity, tool, day)
);
CREATE TABLE IF NOT EXISTS spend (
    identity TEXT NOT NULL,
    day TEXT NOT NULL,
    usd_micros INTEGER NOT NULL,
    PRIMARY KEY (identity, day)
);
CREATE TABLE IF NOT EXISTS receipts (
    payment_id TEXT PRIMARY KEY,
    tool TEXT NOT NULL,
    identity TEXT NOT NULL,
    amount_usd REAL NOT NULL,
    tx_hash TEXT NOT NULL,
    network TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS api_keys (
    key_hash TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    balance_micros INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL
);
"""


class StoreDB:
    """Thread-safe SQLite adapter for all store state."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def _exec(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    # ── replay ───────────────────────────────────────────────────────────
    def claim(self, key: str) -> bool:
        """Atomically claim a nonce/tx. False means already consumed."""
        try:
            self._exec("INSERT INTO consumed(key, consumed_at) VALUES (?, ?)", (key, time.time()))
            return True
        except sqlite3.IntegrityError:
            return False

    def release(self, key: str) -> None:
        """Release a claim that failed before settlement."""
        self._exec("DELETE FROM consumed WHERE key = ?", (key,))

    # ── free tier ────────────────────────────────────────────────────────
    def free_used(self, identity: str, tool: str, day: str) -> int:
        row = self._exec(
            "SELECT used FROM free_usage WHERE identity=? AND tool=? AND day=?",
            (identity, tool, day),
        ).fetchone()
        return int(row["used"]) if row else 0

    def consume_free(self, identity: str, tool: str, day: str) -> None:
        self._exec(
            "INSERT INTO free_usage(identity, tool, day, used) VALUES (?,?,?,1) "
            "ON CONFLICT(identity, tool, day) DO UPDATE SET used = used + 1",
            (identity, tool, day),
        )

    # ── budget ───────────────────────────────────────────────────────────
    def spend_today(self, identity: str, day: str) -> int:
        row = self._exec(
            "SELECT usd_micros FROM spend WHERE identity=? AND day=?", (identity, day)
        ).fetchone()
        return int(row["usd_micros"]) if row else 0

    def add_spend(self, identity: str, day: str, usd_micros: int) -> None:
        self._exec(
            "INSERT INTO spend(identity, day, usd_micros) VALUES (?,?,?) "
            "ON CONFLICT(identity, day) DO UPDATE SET usd_micros = usd_micros + ?",
            (identity, day, usd_micros, usd_micros),
        )

    # ── ledger ───────────────────────────────────────────────────────────
    def record_receipt(
        self,
        *,
        payment_id: str,
        tool: str,
        identity: str,
        amount_usd: float,
        tx_hash: str,
        network: str,
    ) -> None:
        self._exec(
            "INSERT OR REPLACE INTO receipts"
            "(payment_id, tool, identity, amount_usd, tx_hash, network, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (payment_id, tool, identity, amount_usd, tx_hash, network, time.time()),
        )

    def recent_receipts(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._exec(
            "SELECT * FROM receipts ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ── prepaid API keys ─────────────────────────────────────────────────
    def key_balance(self, key_hash: str) -> int | None:
        row = self._exec(
            "SELECT balance_micros, active FROM api_keys WHERE key_hash=?", (key_hash,)
        ).fetchone()
        if not row or not row["active"]:
            return None
        return int(row["balance_micros"])

    def debit_key(self, key_hash: str, micros: int) -> bool:
        cur = self._exec(
            "UPDATE api_keys SET balance_micros = balance_micros - ? "
            "WHERE key_hash=? AND active=1 AND balance_micros >= ?",
            (micros, key_hash, micros),
        )
        return cur.rowcount == 1

    def create_key(self, key_hash: str, label: str, micros: int) -> None:
        self._exec(
            "INSERT INTO api_keys(key_hash, label, balance_micros, created_at) VALUES (?,?,?,?)",
            (key_hash, label, micros, time.time()),
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

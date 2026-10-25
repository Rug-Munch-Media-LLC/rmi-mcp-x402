#!/usr/bin/env python3
"""
RugMunch Data MCP — DEX response core.

The fixed envelope every DEX tool returns, the typed upstream failure, and the
two stateful pieces: a TTL store that also keeps values past TTL for stale
serving, and a per-source rate gate.
"""

import json
import time
from typing import Any, Optional

# source -> base URL, per-second request cap, minimum spacing, default TTL (s)
SOURCES: dict[str, dict] = {
    "dexscreener": {
        "base": "https://api.dexscreener.com",
        "per_sec": 5,
        "min_interval": 0.0,
        "ttl": 30,
        "stale_ttl": 300,
    },
    "geckoterminal": {
        "base": "https://api.geckoterminal.com/api/v2",
        "per_sec": 1,
        "min_interval": 2.0,
        "ttl": 60,
        "stale_ttl": 900,
    },
}


class DataUnavailable(Exception):  # noqa: N818 — contract-fixed name
    """Typed upstream failure. Carries the source and a machine-readable reason."""

    def __init__(self, source: str, reason: str, status: Optional[int] = None) -> None:
        self.source = source
        self.reason = reason
        self.status = status
        super().__init__(f"{source}: {reason}")

    def payload(self) -> dict:
        out: dict = {"type": "DataUnavailable", "source": self.source, "reason": self.reason}
        if self.status is not None:
            out["status"] = self.status
        return out


def envelope(
    source: str,
    data: Any,
    fetched_at: float,
    *,
    stale: bool = False,
    now: Optional[float] = None,
) -> dict:
    """The one response contract: {data, source, fetched_at, stale, age_s}."""
    current = time.time() if now is None else now
    return {
        "data": data,
        "source": source,
        "fetched_at": round(fetched_at, 3),
        "stale": stale,
        "age_s": round(max(0.0, current - fetched_at), 3),
    }


class Store:
    """Insertion-ordered TTL store that KEEPS expired entries for stale serving."""

    def __init__(self, mx: int = 512) -> None:
        self.mx = mx
        self.s: dict[str, tuple[Any, float]] = {}

    def put(self, key: str, value: Any, now: float) -> None:
        if key not in self.s and len(self.s) >= self.mx:
            self.s.pop(next(iter(self.s)))
        self.s[key] = (value, now)

    def peek(self, key: str) -> Optional[tuple[Any, float]]:
        return self.s.get(key)

    def clear(self) -> None:
        self.s.clear()


class Gate:
    """Per-source limiter: a 1-second sliding window plus a minimum interval."""

    def __init__(self, per_sec: int, min_interval: float = 0.0) -> None:
        self.per_sec = max(1, per_sec)
        self.min_interval = min_interval
        self.w: list[float] = []
        self.last = 0.0

    def reserve(self, now: float) -> float:
        """Book the next slot; returns seconds the caller must wait first."""
        self.w = [t for t in self.w if now - t < 1.0]
        wait = 0.0
        if len(self.w) >= self.per_sec:
            wait = max(wait, 1.0 - (now - self.w[0]))
        if self.min_interval and self.last:
            wait = max(wait, self.min_interval - (now - self.last))
        at = now + wait
        self.w.append(at)
        self.last = at
        return round(wait, 3)

    def reset(self) -> None:
        """Drop window state (tests, or an operator clearing a 429 backoff)."""
        self.w.clear()
        self.last = 0.0


def cache_key(source: str, path: str, params: Optional[dict]) -> str:
    return json.dumps([source, path, params or {}], sort_keys=True)


STORE = Store()
GATES: dict[str, Gate] = {
    name: Gate(cfg["per_sec"], cfg["min_interval"]) for name, cfg in SOURCES.items()
}

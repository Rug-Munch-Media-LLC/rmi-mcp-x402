"""Shared doubles for the DEX contract layer: clock, sleeper, client, rig.

Fixtures and injected fakes only — the fleet never monkeypatches, so the
DEX layer exposes a `Rig` seam and these doubles ride it.
"""

from __future__ import annotations

import data_mcp_dex_http as dex_http

Call, Rig = dex_http.Call, dex_http.Rig


class Clock:
    """Injectable clock: `tick` moves time forward without sleeping."""

    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def tick(self, dt: float) -> None:
        self.t += dt


class Sleeps:
    """Sleeper double that records the requested waits instead of sleeping."""

    def __init__(self) -> None:
        self.waits: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waits.append(round(seconds, 3))


class Resp:
    """Minimal httpx.Response stand-in."""

    def __init__(self, status: int = 200, body: object = None, headers: dict | None = None) -> None:
        self.status_code = status
        self._body = body
        self.headers = headers or {}

    def json(self) -> object:
        return self._body


class Client:
    """httpx.AsyncClient-compatible double: one queued item per call."""

    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.calls: list[str] = []

    async def get(self, url: str, params: dict | None = None) -> Resp:
        self.calls.append(url)
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, Exception):
            raise item
        return item  # type: ignore[return-value]


def rig(client: Client, clock: Clock | None = None, sleeps: Sleeps | None = None) -> Rig:
    """Build a Rig with deterministic clock and sleeper."""
    return Rig(client=client, clock=clock or Clock(), sleeper=sleeps or Sleeps())

#!/usr/bin/env python3
"""
RugMunch Data MCP — per-endpoint RPC rate limiter.

One responsibility: a 1-second sliding window per endpoint name, so the RPC
engine can skip an endpoint that is already over its configured rate.
"""

import time


class RL:
    """Per-endpoint 1-second sliding window: `ok` books a slot or refuses it."""

    def __init__(self):
        self.w: dict[str, list[float]] = {}

    def ok(self, ep: str, mx: float) -> bool:
        n = time.monotonic()
        w = [t for t in self.w.get(ep, []) if n - t < 1.0]
        self.w[ep] = w
        if len(w) >= mx:
            return False
        w.append(n)
        return True


_rl = RL()

#!/usr/bin/env python3
"""
RugMunch Data MCP — bounded TTL cache for idempotent RPC reads.

One responsibility: key -> (value, expiry), LRU-evicted at `mx` entries.
"""

import hashlib
import json
import time
from collections import OrderedDict
from typing import Any, Optional


class Cache:
    """Insertion-ordered TTL cache; expired entries are dropped on read."""

    def __init__(self, mx=500):
        self.s: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self.mx = mx

    def _k(self, m: str, p: Any) -> str:
        return hashlib.sha256(f"{m}:{json.dumps(p, sort_keys=True)}".encode()).hexdigest()[:16]

    def get(self, m: str, p: Any, ttl: int = 30) -> Optional[Any]:
        k = self._k(m, p)
        if k in self.s:
            v, e = self.s[k]
            if time.time() < e:
                self.s.move_to_end(k)
                return v
            del self.s[k]
        return None

    def set(self, m: str, p: Any, v: Any, ttl: int = 30):
        k = self._k(m, p)
        if k in self.s:
            self.s.move_to_end(k)
        else:
            if len(self.s) >= self.mx:
                self.s.popitem(last=False)
        self.s[k] = (v, time.time() + ttl)


_cache = Cache()

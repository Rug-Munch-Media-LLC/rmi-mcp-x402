"""MCP protocol-version negotiation (stateless, one decision per connection).

The spec is explicit: if the server supports the version the client asked
for, it MUST reply with that same version; only when it does not may it
answer with another version it supports. Advertising an unimplemented future
version (``2026-07-28`` did exactly this) is neither branch, and every client
we run refuses the handshake over it.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("store.mcp")

# Newest first — ``SUPPORTED_PROTOCOL_VERSIONS[0]`` is also the fallback.
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

NEWEST_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]


def negotiate_protocol_version(params: Any) -> str:
    """Pick the version for one ``initialize`` exchange.

    Echo the client's ``protocolVersion`` when we support it; otherwise fall
    back to the newest supported version and WARN once, naming what was asked
    for. The return value is always inside ``SUPPORTED_PROTOCOL_VERSIONS``.
    """
    requested = params.get("protocolVersion") if isinstance(params, dict) else None
    if requested in SUPPORTED_PROTOCOL_VERSIONS:
        return str(requested)
    if requested:
        log.warning(
            "mcp_protocol_version_unsupported requested=%s newest_supported=%s",
            requested,
            NEWEST_PROTOCOL_VERSION,
        )
    return NEWEST_PROTOCOL_VERSION

"""MCP protocol-version negotiation on ``initialize``.

The store once advertised ``2026-07-28`` — a version no client implements —
so every handshake was refused and the server never registered. The spec says
a supported request MUST be echoed back, and an unsupported one may only be
answered with a version the server actually supports. These four cases pin
that contract plus the tool surface on a negotiated connection.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from store_harness import store_app  # noqa: E402

from store.protocol import SUPPORTED_PROTOCOL_VERSIONS  # noqa: E402


def _initialize(client: object, requested: str | None) -> str:
    """Drive one ``initialize`` and return the negotiated version."""
    params: dict[str, object] = {"capabilities": {}, "clientInfo": {"name": "pytest"}}
    if requested is not None:
        params["protocolVersion"] = requested
    resp = client.post(  # type: ignore[attr-defined]
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": params},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["result"]["protocolVersion"]


def test_supported_version_is_echoed(tmp_path: Path) -> None:
    """Every supported client version comes back unchanged (spec MUST)."""
    with store_app(tmp_path) as h:
        for requested in SUPPORTED_PROTOCOL_VERSIONS:
            assert _initialize(h.client, requested) == requested


def test_opencode_client_version_is_accepted(tmp_path: Path) -> None:
    """The version opencode actually sends must now negotiate."""
    with store_app(tmp_path) as h:
        assert _initialize(h.client, "2024-11-05") == "2024-11-05"


def test_unknown_version_falls_back_to_newest_and_warns_once(
    tmp_path: Path, caplog: logging.LogRecord
) -> None:
    """An unsupported request gets the newest supported version, one WARN."""
    with store_app(tmp_path) as h:
        with caplog.at_level(logging.WARNING, logger="store.mcp"):
            negotiated = _initialize(h.client, "1999-01-01")
    assert negotiated == SUPPORTED_PROTOCOL_VERSIONS[0]
    warns = [r for r in caplog.records if "mcp_protocol_version_unsupported" in r.getMessage()]
    assert len(warns) == 1
    assert "1999-01-01" in warns[0].getMessage()


def test_negotiated_version_is_always_supported(tmp_path: Path) -> None:
    """A missing or unhashable-ish request still lands inside the set."""
    with store_app(tmp_path) as h:
        for requested in (None, "", "2026-07-28", "2024-11-06"):
            assert _initialize(h.client, requested) in SUPPORTED_PROTOCOL_VERSIONS


def test_discovery_document_advertises_a_supported_version(tmp_path: Path) -> None:
    """The /.well-known/x402 doc must not name a version clients cannot take."""
    with store_app(tmp_path) as h:
        body = h.client.get("/.well-known/x402").json()
    assert body["mcp"]["protocolVersion"] in SUPPORTED_PROTOCOL_VERSIONS


def test_tools_list_on_negotiated_connection(tmp_path: Path) -> None:
    """The five paid tools still list after a negotiated handshake."""
    with store_app(tmp_path) as h:
        assert _initialize(h.client, "2024-11-05") == "2024-11-05"
        resp = h.client.post(
            "/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        )
    names = {t["name"] for t in resp.json()["result"]["tools"]}
    assert names == {
        "intel.scan",
        "scanner.token",
        "security.wallet",
        "news.search",
        "exploit.lookup",
    }

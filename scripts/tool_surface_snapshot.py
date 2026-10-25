#!/usr/bin/env python3
"""tool_surface_snapshot.py — pin or verify the data MCP tool surface.

The surface is what agents actually receive from `tools/list`: tool names,
descriptions and input schemas. A refactor may move code between modules; it
must not move the surface. This script reads the live surface and either
writes the pin (`--update`) or reports drift against it (default).

Usage:
  python3 scripts/tool_surface_snapshot.py           # report drift, rc=1 if any
  python3 scripts/tool_surface_snapshot.py --update  # rewrite the pin deliberately
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PIN = REPO / "tests" / "fixtures" / "tool_surface.json"


def current_surface() -> dict:
    """The live surface, in the shape the pin stores it."""
    sys.path.insert(0, str(REPO))

    import data_mcp  # imported here so --help stays cheap and side-effect free

    tools = asyncio.run(data_mcp.mcp.list_tools())
    return {
        tool.name: {"description": tool.description or "", "input_schema": tool.parameters}
        for tool in sorted(tools, key=lambda t: t.name)
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--update", action="store_true", help="rewrite the pin")
    args = ap.parse_args()

    live = current_surface()
    if args.update:
        PIN.write_text(json.dumps(live, indent=2, sort_keys=True) + "\n")
        print(f"pinned {len(live)} tools -> {PIN.relative_to(REPO)}")
        return 0

    pinned = json.loads(PIN.read_text()) if PIN.exists() else {}
    drift = [
        f"{name}: {'added' if name not in pinned else 'changed'}"
        for name in live
        if pinned.get(name) != live[name]
    ]
    removed = sorted(set(pinned) - set(live))
    if removed:
        drift.extend(f"{name}: removed" for name in removed)
    if drift:
        print("tool surface DRIFT (intentional? run --update):")
        for line in drift:
            print(f"  - {line}")
        return 1
    print(f"tool surface: {len(live)} tools, no drift")
    return 0


if __name__ == "__main__":
    sys.exit(main())

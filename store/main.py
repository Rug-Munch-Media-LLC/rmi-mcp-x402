"""Entry point: uvicorn app on the store port (default 8026).

Port 8026 is deliberate: 8000 is rmi-backend, 8001 is Coffer, 12008 is
MetaMCP, 9090-9131 are the MCP bridges. The store must never collide with
the facilitator it settles through.
"""

from __future__ import annotations

import logging
import os

import uvicorn

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def main() -> None:
    """Run the store."""
    host = os.getenv("STORE_HOST", "127.0.0.1")
    port = int(os.getenv("STORE_PORT", "8026"))
    uvicorn.run("store.app:app", host=host, port=port, log_level="info", access_log=False)


if __name__ == "__main__":
    main()

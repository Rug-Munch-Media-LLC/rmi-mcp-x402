"""AI Guard integration for the x402 MCP server.

Works standalone or with the app.security module when available.
"""

from __future__ import annotations

import logging
import os

from fastapi import Request

logger = logging.getLogger("x402_mcp_server")


class AIGuardWrapper:
    """AI Guard that works standalone or with app.security."""

    def __init__(self):
        self.active = False
        self.blocked_patterns = [
            "drop table",
            "delete from",
            "insert into",
            "--",
            "/*",
            "xp_",
            "union select",
            "exec(",
            "eval(",
            "<script",
            "javascript:",
            "onload=",
            "onerror=",
            "document.cookie",
            "localStorage",
            "SELECT * FROM",
            "DROP TABLE",
            "INSERT INTO",
            "DELETE FROM",
        ]
        self._load_external_guard()

    def _load_external_guard(self):
        """Try to load RugMunch's actual AI Guard if available."""
        try:
            import sys

            sys.path.insert(0, os.environ.get("RMI_BACKEND_PATH", "/backend/app"))
            from security.ai_guard import AIGuard

            self._guard = AIGuard()
            self.active = True
            logger.info("✅ AI Guard loaded from RugMunch security module")
        except Exception as e:
            logger.warning(f"⚠️ Using standalone AI Guard (external module unavailable: {e})")
            self._guard = None
            self.active = True  # Standalone still works

    async def check_request(self, request: Request) -> tuple[bool, str | None]:
        """Check request for security violations."""
        # Check headers
        user_agent = request.headers.get("User-Agent", "").lower()
        if any(bot in user_agent for bot in ["sqlmap", "nikto", "nmap", "masscan", "zgrab"]):
            return False, "Security scanner detected"

        # Check body for POST/PUT/PATCH
        if request.method in ("POST", "PUT", "PATCH"):
            try:
                body = await request.body()
                body_str = body.decode("utf-8", errors="ignore").lower()
                for pattern in self.blocked_patterns:
                    if pattern in body_str:
                        return False, f"Malicious pattern detected: {pattern}"
            except Exception:
                pass

        return True, None

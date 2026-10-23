"""AI Guard integration for the x402 MCP server.

Works standalone or with the app.security module when available.
"""

from __future__ import annotations

import logging
import os

from fastapi import Request

logger = logging.getLogger("x402_mcp_server")

# Structured, non-leaking fail-closed reasons. These are the only strings
# surfaced to clients — never raw exception text or body contents.
REASON_BODY_UNREADABLE = "AI_GUARD_BODY_UNREADABLE"
REASON_BODY_NOT_UTF8 = "AI_GUARD_BODY_NOT_UTF8"
REASON_PATTERN = "AI_GUARD_PATTERN"
REASON_SCANNER = "AI_GUARD_SCANNER"


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
        # Body scan is case-normalized on BOTH sides — the original list
        # mixed lower- and upper-case patterns against a lowered body, so
        # "SELECT * FROM" (upper) could never match. Precompute once.
        self._blocked_lower = [pattern.lower() for pattern in self.blocked_patterns]
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
            return False, REASON_SCANNER

        # Check body for POST/PUT/PATCH. FAIL CLOSED (2026-10-23 hardening):
        # an unreadable or non-UTF-8 body is treated as a violation, never
        # silently passed through — an unreadable body cannot be scanned, so
        # allowing it on would be a security bypass.
        if request.method in ("POST", "PUT", "PATCH"):
            try:
                body = await request.body()
            except Exception:
                logger.warning(
                    "ai_guard: request body unreadable — failing closed",
                    exc_info=True,
                )
                return False, REASON_BODY_UNREADABLE
            if _body_is_malformed(body):
                return False, REASON_BODY_NOT_UTF8
            lowered = body.decode("utf-8").lower()
            for pattern in self._blocked_lower:
                if pattern in lowered:
                    logger.info("ai_guard: request blocked (%s)", REASON_PATTERN)
                    return False, REASON_PATTERN

        return True, None


def _body_is_malformed(body: bytes) -> bool:
    """Return True when ``body`` cannot be decoded as UTF-8.

    Unguarded bodies are treated as malformed (fail closed): an MCP/HTTP
    JSON gateway never sends binary payloads, so undecodable input is a
    probe, not a legitimate request.
    """
    try:
        body.decode("utf-8")
    except UnicodeDecodeError:
        logger.info("ai_guard: malformed (non-UTF-8) request body blocked")
        return True
    return False

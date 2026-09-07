"""Signed-admin-token auth for the Verified Badge admin endpoints.

The ``/admin/revoke/...`` and ``/admin/renew/...`` routes mutate badge state
and MUST reject anonymous calls — the 2026-09-06 service-health audit found
them wide open. Callers authenticate with an HMAC-SHA256 signature over the
canonical message ``"{method} {path} {timestamp}"`` using the shared
``VERIFIED_ADMIN_SECRET``::

    X-Admin-Timestamp: <unix epoch seconds>
    X-Admin-Signature: hex(hmac_sha256(secret, "POST /api/v1/verified/admin/revoke/... 1710000000"))

The timestamp bounds replay (default ±5 minutes) and verification is
constant-time (``hmac.compare_digest``). The whole check fails CLOSED: an
unset secret disables the admin routes rather than opening them.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time

from fastapi import HTTPException, Request, status

#: Env var holding the shared admin signing secret (never hardcoded).
ADMIN_SECRET_ENV = "VERIFIED_ADMIN_SECRET"
#: Max clock skew (seconds) tolerated between the client timestamp and now.
MAX_SKEW_SECONDS = 300

__all__ = [
    "ADMIN_SECRET_ENV",
    "MAX_SKEW_SECONDS",
    "require_signed_admin",
    "sign_admin_request",
]


def sign_admin_request(secret: str, method: str, path: str, timestamp: str) -> str:
    """HMAC-SHA256 hex digest over ``"{method} {path} {timestamp}"``."""
    message = f"{method} {path} {timestamp}"
    return hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def _timestamp_valid(timestamp: str) -> bool:
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    return abs(time.time() - ts) <= MAX_SKEW_SECONDS


async def require_signed_admin(request: Request) -> None:
    """FastAPI dependency: 401 unless the request carries a valid admin signature."""
    secret = os.getenv(ADMIN_SECRET_ENV, "").strip()
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"admin signing secret not configured ({ADMIN_SECRET_ENV})",
        )
    provided = request.headers.get("X-Admin-Signature", "")
    timestamp = request.headers.get("X-Admin-Timestamp", "")
    if not provided or not timestamp:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="signed admin token required (X-Admin-Signature + X-Admin-Timestamp headers)",
        )
    if not _timestamp_valid(timestamp):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="admin signature timestamp missing, invalid, or expired",
        )
    expected = sign_admin_request(secret, request.method, str(request.url.path), timestamp)
    if not hmac.compare_digest(expected, provided):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid admin signature",
        )

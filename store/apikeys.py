"""Prepaid API keys — issuance and hashing.

Keys are never stored in clear: SHA-256 over (pepper, key) with the pepper in
the environment. A leaked SQLite file yields no usable credential.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets

_PREFIX = "rmi_store_"


def _pepper() -> str:
    return os.getenv("STORE_KEY_PEPPER", "")


def hash_key(raw_key: str) -> str:
    """Stable hash used as the primary key of the api_keys table."""
    pepper = _pepper().encode()
    return hashlib.sha256(pepper + raw_key.encode()).hexdigest()


def issue_key() -> str:
    """Generate a new prepaid key (shown once, at creation)."""
    return f"{_PREFIX}{secrets.token_urlsafe(32)}"


def looks_like_key(value: str) -> bool:
    """Cheap shape check before touching the database."""
    return bool(value) and value.startswith(_PREFIX) and len(value) > len(_PREFIX) + 20


def constant_time_equal(left: str, right: str) -> bool:
    """Timing-safe comparison for admin secrets."""
    return hmac.compare_digest(left.encode(), right.encode())

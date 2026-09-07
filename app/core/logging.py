"""Lightweight logging helper shared across RugMunch service modules.

Kept deliberately thin: a module-level ``get_logger(name)`` returning a
stdlib :class:`logging.Logger` with a fleet-friendly stream handler, so any
module can call ``get_logger(__name__)`` without repeating ``logging``
plumbing. Importing this module never blocks on external services.
"""

from __future__ import annotations

import logging
import sys

_CONFIGURED = False


def _configure_root() -> None:
    """Install a stderr handler once (idempotent; safe to call repeatedly)."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    if root.level == logging.NOTSET:
        root.setLevel(logging.INFO)


def get_logger(name: str) -> logging.Logger:
    """Return a configured logger for ``name`` (stdlib only, no side effects)."""
    _configure_root()
    return logging.getLogger(name)

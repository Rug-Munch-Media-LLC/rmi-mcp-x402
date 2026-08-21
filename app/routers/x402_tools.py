"""
x402 Tools — compatibility shim.

All functionality moved to app/routers/x402.py (thin mount of the spec-compliant
domain surface: app.domain.x402.router + app.domain.x402.tools_router). Kept for
backward compat with files that still import from this module.
"""

# The old router is no longer mounted here — routes moved to app/routers/x402.py
# Import the consolidated router for combined use if needed.
from app.routers.x402 import router  # noqa: F401

__all__ = ["router"]

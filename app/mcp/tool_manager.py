"""X402ToolManager — manages the complete x402 tool bundle catalog."""

from __future__ import annotations

from app.mcp.bundles import load_core_bundles
from app.mcp.bundles_premium import load_premium_bundles
from app.mcp.models import X402Bundle


class X402ToolManager:
    """Manages all x402 tool bundles including free/trial/premium."""

    def __init__(self):
        self._bundles = self._load_all_bundles()
        self._index = {b.bundle_id: b for b in self._bundles}
        self._index.update({b.name.lower(): b for b in self._bundles})

    def _load_all_bundles(self) -> list[X402Bundle]:
        """Load complete catalog: free, trial, standard, premium."""
        return load_core_bundles() + load_premium_bundles()

    def list_bundles(self, tier: str | None = None) -> list[X402Bundle]:
        """List all or filtered bundles."""
        if tier:
            return [b for b in self._bundles if b.tier == tier]
        return self._bundles

    def get_bundle(self, identifier: str) -> X402Bundle | None:
        return self._index.get(identifier.lower())

    @property
    def free_bundles(self) -> list[X402Bundle]:
        return [b for b in self._bundles if b.tier == "free"]

    @property
    def trial_bundles(self) -> list[X402Bundle]:
        return [b for b in self._bundles if b.tier == "trial"]

    @property
    def premium_bundles(self) -> list[X402Bundle]:
        return [b for b in self._bundles if b.is_premium]

    @property
    def total_endpoints(self) -> int:
        return sum(len(b.endpoints) for b in self._bundles)

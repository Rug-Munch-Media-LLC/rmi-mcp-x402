"""x402 tool bundle catalog — free, trial, and standard tiers.

Pure data. The X402ToolManager in tool_manager.py consumes these.
"""

from __future__ import annotations

from app.mcp.models import X402Bundle


def load_core_bundles() -> list[X402Bundle]:
    """Load free, trial, and standard tier bundles."""
    return [
        # ========== FREE TIER ==========
        X402Bundle(
            bundle_id="free-health",
            name="Free Health Check",
            description="Free system health and status endpoints",
            category="free",
            endpoints=[
                {
                    "method": "GET",
                    "path": "/health",
                    "description": "System health",
                },
                {
                    "method": "GET",
                    "path": "/status",
                    "description": "System status",
                },
                {"method": "GET", "path": "/version", "description": "API version"},
            ],
            price_usd=0.0,
            tier="free",
            is_premium=False,
            requires_payment=False,
        ),
        X402Bundle(
            bundle_id="free-discovery",
            name="Free Tool Discovery",
            description="Discover all available tools without payment",
            category="free",
            endpoints=[
                {
                    "method": "GET",
                    "path": "/.well-known/x402",
                    "description": "x402 discovery",
                },
                {
                    "method": "GET",
                    "path": "/v1/bundles",
                    "description": "List all bundles",
                },
                {
                    "method": "GET",
                    "path": "/v1/bundles/{id}",
                    "description": "Bundle details",
                },
            ],
            price_usd=0.0,
            tier="free",
            is_premium=False,
            requires_payment=False,
        ),
        # ========== TRIAL TIER ==========
        X402Bundle(
            bundle_id="trial-security",
            name="Trial Security Suite",
            description="7-day trial of premium security tools",
            category="trial",
            endpoints=[
                {
                    "method": "POST",
                    "path": "/scan/contract",
                    "description": "Smart contract scan",
                },
                {
                    "method": "POST",
                    "path": "/scan/token",
                    "description": "Token risk scan",
                },
                {
                    "method": "POST",
                    "path": "/scan/wallet",
                    "description": "Wallet risk scan",
                },
            ],
            price_usd=0.0,
            tier="trial",
            is_premium=False,
            requires_payment=False,
        ),
        X402Bundle(
            bundle_id="trial-intelligence",
            name="Trial Intelligence Suite",
            description="7-day trial of market intelligence tools",
            category="trial",
            endpoints=[
                {
                    "method": "GET",
                    "path": "/market/pulse",
                    "description": "Token pulse",
                },
                {
                    "method": "GET",
                    "path": "/market/trends",
                    "description": "Market trends",
                },
                {
                    "method": "GET",
                    "path": "/market/whales",
                    "description": "Whale tracking",
                },
            ],
            price_usd=0.0,
            tier="trial",
            is_premium=False,
            requires_payment=False,
        ),
        # ========== STANDARD TIER ==========
        X402Bundle(
            bundle_id="token-pulse",
            name="Token Pulse",
            description="Real-time market momentum, volume, whale alerts",
            category="market",
            endpoints=[
                {
                    "method": "GET",
                    "path": "/tokens",
                    "description": "Token listings",
                },
                {
                    "method": "GET",
                    "path": "/tokens/{address}",
                    "description": "Token details",
                },
                {
                    "method": "GET",
                    "path": "/lp/{address}",
                    "description": "Liquidity pool analysis",
                },
            ],
            price_usd=0.01,
            tier="standard",
        ),
        X402Bundle(
            bundle_id="url-scam-detector",
            name="URL Scam Detector",
            description="Detect phishing sites, fake docs, malicious redirects",
            category="security",
            endpoints=[
                {"method": "POST", "path": "/scan/url", "description": "URL scan"},
                {
                    "method": "POST",
                    "path": "/scan/domain",
                    "description": "Domain analysis",
                },
            ],
            price_usd=0.01,
            tier="standard",
        ),
        X402Bundle(
            bundle_id="wallet-profiler",
            name="Wallet Profiler",
            description="Full wallet analysis with persona detection",
            category="wallet",
            endpoints=[
                {
                    "method": "POST",
                    "path": "/wallet/profile",
                    "description": "Profile wallet",
                },
                {
                    "method": "POST",
                    "path": "/wallet/history",
                    "description": "Transaction history",
                },
            ],
            price_usd=0.05,
            tier="standard",
        ),
        X402Bundle(
            bundle_id="social-sentiment",
            name="Social Sentiment",
            description="Cross-platform sentiment analysis",
            category="market",
            endpoints=[
                {
                    "method": "POST",
                    "path": "/sentiment/analyze",
                    "description": "Analyze sentiment",
                },
                {
                    "method": "GET",
                    "path": "/sentiment/trends",
                    "description": "Trending topics",
                },
            ],
            price_usd=0.03,
            tier="standard",
        ),
    ]

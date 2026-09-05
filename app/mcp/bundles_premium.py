"""x402 tool bundle catalog — premium tier.

Pure data. The X402ToolManager in tool_manager.py consumes these.
"""

from __future__ import annotations

from app.mcp.models import X402Bundle


def load_premium_bundles() -> list[X402Bundle]:
    """Load premium tier bundles."""
    return [
        X402Bundle(
            bundle_id="suspicious-transfers",
            name="Suspicious Transfers Scanner",
            description="Cross-chain anomaly detection with RugMunch intelligence",
            category="security",
            endpoints=[
                {
                    "method": "POST",
                    "path": "/scan/transfers",
                    "description": "Scan transfers",
                },
                {
                    "method": "GET",
                    "path": "/scan/results",
                    "description": "Get results",
                },
                {
                    "method": "POST",
                    "path": "/scan/anomaly",
                    "description": "Anomaly detection",
                },
            ],
            price_usd=0.15,
            tier="premium",
            is_premium=True,
        ),
        X402Bundle(
            bundle_id="wallet-labeler",
            name="Wallet Labeler",
            description="Behavioral reputation engine (Arkham+Nansen+Alchemy)",
            category="security",
            endpoints=[
                {
                    "method": "POST",
                    "path": "/label/wallet",
                    "description": "Label wallet",
                },
                {
                    "method": "GET",
                    "path": "/label/history",
                    "description": "Label history",
                },
                {
                    "method": "POST",
                    "path": "/label/cluster",
                    "description": "Cluster detection",
                },
            ],
            price_usd=0.20,
            tier="premium",
            is_premium=True,
        ),
        X402Bundle(
            bundle_id="memory-bank",
            name="Memory Bank",
            description="Unified agent knowledge store with GCS export",
            category="ai",
            endpoints=[
                {
                    "method": "POST",
                    "path": "/memory/store",
                    "description": "Store knowledge",
                },
                {
                    "method": "GET",
                    "path": "/memory/query",
                    "description": "Query knowledge",
                },
                {
                    "method": "POST",
                    "path": "/memory/export",
                    "description": "Export to GCS",
                },
            ],
            price_usd=0.12,
            tier="premium",
            is_premium=True,
        ),
        X402Bundle(
            bundle_id="deep-contract-audit",
            name="Deep Contract Audit",
            description="Smart contract audit, honeypot detection, hidden mint functions",
            category="security",
            endpoints=[
                {
                    "method": "POST",
                    "path": "/audit/contract",
                    "description": "Full contract audit",
                },
                {
                    "method": "POST",
                    "path": "/audit/honeypot",
                    "description": "Honeypot check",
                },
                {
                    "method": "POST",
                    "path": "/audit/proxy",
                    "description": "Proxy pattern check",
                },
            ],
            price_usd=0.05,
            tier="premium",
            is_premium=True,
        ),
        X402Bundle(
            bundle_id="token-forensics",
            name="Token Forensics",
            description="Deep forensics from DexScreener, GeckoTerminal, CoinGecko",
            category="forensics",
            endpoints=[
                {
                    "method": "POST",
                    "path": "/forensics/token",
                    "description": "Token forensics",
                },
                {
                    "method": "POST",
                    "path": "/forensics/report",
                    "description": "Generate report",
                },
            ],
            price_usd=0.10,
            tier="premium",
            is_premium=True,
        ),
        X402Bundle(
            bundle_id="whale-decoder",
            name="Whale Decoder",
            description="Advanced whale wallet analysis across chains",
            category="wallet",
            endpoints=[
                {
                    "method": "POST",
                    "path": "/whale/decode",
                    "description": "Decode whale strategy",
                },
                {
                    "method": "GET",
                    "path": "/whale/positions",
                    "description": "Track positions",
                },
            ],
            price_usd=0.15,
            tier="premium",
            is_premium=True,
        ),
        X402Bundle(
            bundle_id="darkroom-security",
            name="Darkroom Security Suite",
            description="Advanced security analysis, threat detection, forensics",
            category="security",
            endpoints=[
                {
                    "method": "POST",
                    "path": "/security/scan",
                    "description": "Security scan",
                },
                {
                    "method": "POST",
                    "path": "/security/threats",
                    "description": "Threat database",
                },
                {
                    "method": "POST",
                    "path": "/security/forensics",
                    "description": "Forensic analysis",
                },
            ],
            price_usd=0.20,
            tier="premium",
            is_premium=True,
        ),
        X402Bundle(
            bundle_id="solana-mcp",
            name="Solana MCP Server",
            description="Native Solana blockchain queries via MCP protocol",
            category="mcp",
            endpoints=[
                {
                    "method": "POST",
                    "path": "/mcp/solana/balance",
                    "description": "Get SOL balance",
                },
                {
                    "method": "POST",
                    "path": "/mcp/solana/transaction",
                    "description": "Get transaction",
                },
                {
                    "method": "POST",
                    "path": "/mcp/solana/tokens",
                    "description": "Get token accounts",
                },
                {
                    "method": "POST",
                    "path": "/mcp/solana/simulate",
                    "description": "Simulate transaction",
                },
            ],
            price_usd=0.08,
            tier="premium",
            is_premium=True,
        ),
        X402Bundle(
            bundle_id="ethereum-mcp",
            name="Ethereum MCP Server",
            description="Native Ethereum blockchain queries via MCP protocol",
            category="mcp",
            endpoints=[
                {
                    "method": "POST",
                    "path": "/mcp/eth/balance",
                    "description": "Get ETH balance",
                },
                {
                    "method": "POST",
                    "path": "/mcp/eth/transaction",
                    "description": "Get transaction",
                },
                {
                    "method": "POST",
                    "path": "/mcp/eth/call",
                    "description": "Call contract",
                },
                {
                    "method": "POST",
                    "path": "/mcp/eth/logs",
                    "description": "Get event logs",
                },
                {
                    "method": "POST",
                    "path": "/mcp/eth/ens",
                    "description": "Resolve ENS",
                },
            ],
            price_usd=0.08,
            tier="premium",
            is_premium=True,
        ),
    ]

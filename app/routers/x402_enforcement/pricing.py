"""x402_enforcement.pricing — TOOL_PRICES catalog + loaders."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("x402_enforcement_v2")

# ── Tool pricing (parsed from gateway configs) ──
TOOL_PRICES: dict[str, dict[str, Any]] = {}


def _load_tool_prices():
    """Load tool prices from canonical dict (single source of truth).

    1. Load from CANONICAL_TOOL_PRICES (127 tools, always present)
    2. Enrich from gateway configs if available (adds chain variants)
    3. NO circular catalog API fallback — that was the bug
    """
    import os
    import re

    # ─── 1. Load canonical base (ALWAYS works, no circular dependency) ────
    from app.canonical_tools import CANONICAL_TOOL_PRICES

    TOOL_PRICES.update(CANONICAL_TOOL_PRICES)

    # ─── 2. Enrich from gateway configs (adds chain-specific variants) ────────
    # Try multiple paths: Docker mount, host path, OpenClaw backup
    gateway_base = None
    for candidate in [
        "/app/x402-gateway",
        "x402-gateway",  # fallback
        os.environ.get("RMI_X402_GATEWAY_PATH", "/app/x402-gateway"),
        "x402-gateway",  # fallback
    ]:
        if os.path.isdir(candidate):
            gateway_base = candidate
            break

    if gateway_base and os.path.exists(gateway_base):
        logger.info(f"Loading tool prices from gateway configs at {gateway_base}")
        for chain_dir in os.listdir(gateway_base):
            index_path = os.path.join(gateway_base, chain_dir, "index.ts")
            if not os.path.exists(index_path):
                continue
            with open(index_path) as f:
                content = f.read()

            # Flexible regex to extract key-value pairs from tool definitions
            tool_pattern = r"(\w+):\s*\{([^}]+)\}"
            for m in re.finditer(tool_pattern, content):
                tool_id = m.group(1)
                body = m.group(2)
                if "name:" not in body or "price:" not in body:
                    continue

                # Extract individual fields
                name_match = re.search(r'name:\s*"([^"]+)"', body)
                price_match = re.search(r'price:\s*"\$([^"]+)"', body)
                atoms_match = re.search(r'priceAtomic:\s*"([^"]+)"', body)
                cat_match = re.search(r'category:\s*"([^"]+)"', body)
                trial_match = re.search(r"trialFree:\s*(\d+)", body)

                if all([name_match, price_match, atoms_match, cat_match, trial_match]):
                    TOOL_PRICES[tool_id] = {
                        "price_usd": float(price_match.group(1)),  # type: ignore[union-attr]
                        "price_atoms": atoms_match.group(1),  # type: ignore[union-attr]
                        "category": (cat_match.group(1) or "").lower(),  # type: ignore[union-attr]
                        "trial_free": int(trial_match.group(1)),  # type: ignore[union-attr]
                        "description": name_match.group(1) or "",  # type: ignore[union-attr]
                    }

    # ─── 2. Add manual pricing for new tools ────────────────────────────────
    _new_tool_prices = {
        "forensic_valuation": {
            "price_usd": 0.25,
            "price_atoms": "250000",
            "category": "premium",
            "trial_free": 1,
            "description": "Institutional-grade token valuation — DCF intrinsic value, comparable analysis with outlier detection, scam probability scoring",
        },
        "osint_identity_hunt": {
            "price_usd": 0.15,
            "price_atoms": "150000",
            "category": "premium",
            "trial_free": 2,
            "description": "Cross-platform OSINT investigation — hunt usernames across 400+ networks, domain intelligence, stealth page capture",
        },
        "investigation_report": {
            "price_usd": 0.20,
            "price_atoms": "200000",
            "category": "premium",
            "trial_free": 1,
            "description": "Full investigation report — on-chain forensics, financial valuation, OSINT findings, scam scoring in one deliverable",
        },
        "whale_accumulation": {
            "price_usd": 0.10,
            "price_atoms": "100000",
            "category": "intelligence",
            "trial_free": 2,
            "description": "Accumulation Pattern Detector — detect stealth accumulation by large holders before price impact. Identifies quiet buying patterns and wallet funding sequences.",
        },
        "social_engineering": {
            "price_usd": 0.15,
            "price_atoms": "150000",
            "category": "premium",
            "trial_free": 1,
            "description": "Social Engineering & Identity Fraud Detector — detect fake teams, AI-generated profiles, phishing domains, copied whitepapers, and social engineering campaigns.",
        },
        "forensic_pack": {
            "price_usd": 0.35,
            "price_atoms": "350000",
            "category": "bundle",
            "trial_free": 1,
            "description": "Forensic Investigation Pack — valuation + OSINT + report at 33% discount",
        },
        "token_watch_create": {
            "price_usd": 0.05,
            "price_atoms": "50000",
            "category": "monitoring",
            "trial_free": 3,
            "description": "Set token monitoring watch — alerts when LP drops, price changes, or rug indicators detected",
        },
        "token_watch_check": {
            "price_usd": 0.03,
            "price_atoms": "30000",
            "category": "monitoring",
            "trial_free": 5,
            "description": "One-shot token status check — current LP, price, volume, and rug risk warnings",
        },
    }
    TOOL_PRICES.update(_new_tool_prices)

    # ─── 3. NO MORE circular catalog API fallback ─────────────────────────
    # Removed: was causing circular dependency (catalog reads from TOOL_PRICES,
    # which loads from catalog). Canonical_tools.py is now the source of truth.


# ── Add new tools from x402_tools.py that aren't in gateway configs yet ──
try:
    _NEW_TOOL_PRICES = {
        "forensic_valuation": {
            "price_usd": 0.25,
            "price_atoms": "250000",
            "category": "premium",
            "trial_free": 1,
            "description": "Institutional-grade token valuation — DCF intrinsic value, comparable analysis with outlier detection, scam probability scoring",
        },
        "osint_identity_hunt": {
            "price_usd": 0.15,
            "price_atoms": "150000",
            "category": "premium",
            "trial_free": 2,
            "description": "Cross-platform OSINT investigation — hunt usernames across 400+ networks, domain intelligence, stealth page capture",
        },
        "whale_accumulation": {
            "price_usd": 0.10,
            "price_atoms": "100000",
            "category": "intelligence",
            "trial_free": 2,
            "description": "Accumulation Pattern Detector — detect stealth accumulation by large holders before price impact. Identifies quiet buying patterns and wallet funding sequences.",
        },
        "social_engineering": {
            "price_usd": 0.15,
            "price_atoms": "150000",
            "category": "premium",
            "trial_free": 1,
            "description": "Social Engineering & Identity Fraud Detector — detect fake teams, AI-generated profiles, phishing domains, copied whitepapers, and social engineering campaigns.",
        },
        "forensic_pack": {
            "price_usd": 0.35,
            "price_atoms": "350000",
            "category": "bundle",
            "trial_free": 1,
            "description": "Forensic Investigation Pack — valuation + OSINT + report at 33% discount",
        },
        # API / meta tools — must be in TOOL_PRICES at startup (not loaded from catalog)
        "catalog": {
            "price_usd": 0.00,
            "price_atoms": "0",
            "category": "api",
            "trial_free": 999,
            "description": "Browse available tools, pricing, and chain support",
        },
        "smart_money_alpha": {
            "price_usd": 0.01,
            "price_atoms": "10000",
            "category": "intelligence",
            "trial_free": 3,
            "description": "Smart money alpha signals — track wallets that consistently outperform the market",
        },
        "meme_vibe_score": {
            "price_usd": 0.01,
            "price_atoms": "10000",
            "category": "social",
            "trial_free": 3,
            "description": "Meme token vibe scoring — sentiment, community strength, and virality analysis",
        },
        "mcp-proxy": {
            "price_usd": 0.01,
            "price_atoms": "10000",
            "category": "api",
            "trial_free": 5,
            "description": "MCP protocol proxy — route tool calls through the x402 payment layer",
        },
        "human-execute": {
            "price_usd": 0.02,
            "price_atoms": "20000",
            "category": "api",
            "trial_free": 2,
            "description": "Human-in-the-loop execution — wallet-based payment for manual crypto investigation tasks",
        },
        # Premium standout tools
        "reputation_score": {
            "price_usd": 0.10,
            "price_atoms": "100000",
            "category": "premium",
            "trial_free": 1,
            "description": "Comprehensive 0-100 trust score combining wallet labels, scam databases, deployer history, and RAG similarity matching",
        },
        "webhook_register": {
            "price_usd": 0.02,
            "price_atoms": "20000",
            "category": "monitoring",
            "trial_free": 2,
            "description": "Register webhook URL for real-time monitoring alerts — rug pulls, whale moves, price crashes",
        },
        "webhook_list": {
            "price_usd": 0.00,
            "price_atoms": "0",
            "category": "monitoring",
            "trial_free": 999,
            "description": "List registered webhooks for an address",
        },
        "investigation_report": {
            "price_usd": 0.25,
            "price_atoms": "250000",
            "category": "premium",
            "trial_free": 1,
            "description": "AI-generated comprehensive investigation report — reputation, forensics, market context, recommendation",
        },
        # Advanced standout tools
        "rug_probability": {
            "price_usd": 0.15,
            "price_atoms": "150000",
            "category": "premium",
            "trial_free": 1,
            "description": "Predictive rug pull probability 0-100 — honeypot + liquidity + deployer + social signals",
        },
        "history": {
            "price_usd": 0.08,
            "price_atoms": "80000",
            "category": "analysis",
            "trial_free": 2,
            "description": "Historical scanner time-series — risk/liquidity/volume/price trends over hours",
        },
        "narrative": {
            "price_usd": 0.05,
            "price_atoms": "50000",
            "category": "social",
            "trial_free": 3,
            "description": "Market narrative engine — what is the market saying about this token RIGHT NOW",
        },
        # Institutional tools
        "portfolio_risk": {
            "price_usd": 0.20,
            "price_atoms": "200000",
            "category": "premium",
            "trial_free": 1,
            "description": "Cross-chain portfolio risk dashboard — unified risk across multiple wallets and chains",
        },
        "defi_position": {
            "price_usd": 0.15,
            "price_atoms": "150000",
            "category": "defi",
            "trial_free": 1,
            "description": "DeFi position analyzer — LP holdings, impermanent loss estimation, yield sustainability, protocol risk",
        },
        "liquidation_cascade": {
            "price_usd": 0.15,
            "price_atoms": "150000",
            "category": "defi",
            "trial_free": 1,
            "description": "Liquidation Cascade Risk Analyzer — cross-chain DeFi position monitoring, health factor computation, and cascade scenario simulation for Aave/Compound positions",
        },
        "mev_detect": {
            "price_usd": 0.15,
            "price_atoms": "150000",
            "category": "security",
            "trial_free": 2,
            "description": "MEV/Sandwich attack detection — sandwich attacks, frontrunning, arbitrage extraction, MEV bot identification",
        },
        # Alpha tools
        "composite_score": {
            "price_usd": 0.25,
            "price_atoms": "250000",
            "category": "premium",
            "trial_free": 1,
            "description": "RMI Composite Score — one number combining ALL signals for instant buy/sell/avoid decisions",
        },
        "smart_money": {
            "price_usd": 0.20,
            "price_atoms": "200000",
            "category": "intelligence",
            "trial_free": 1,
            "description": "Smart Money P&L Tracker — real profitability-based wallet tracking, find the actual profitable traders",
        },
        "clone_detect": {
            "price_usd": 0.10,
            "price_atoms": "100000",
            "category": "security",
            "trial_free": 2,
            "description": "Token Clone Detector — find tokens cloned from known rug pulls by name, symbol, and deployer patterns",
        },
        "wash_trade_detect": {
            "price_usd": 0.15,
            "price_atoms": "150000",
            "category": "security",
            "trial_free": 2,
            "description": "Wash Trading & Insider Detection — artificial volume, coordinated buying, insider accumulation patterns",
        },
        # RIP: Rug Pull Imminence Predictor
        "rug_imminence": {
            "price_usd": 0.20,
            "price_atoms": "200000",
            "category": "security",
            "trial_free": 1,
            "description": "Rug Pull Imminence Predictor — AI-powered early warning system fusing 7 signals to predict imminent rug pulls before they happen",
        },
        "cross_chain_whale": {
            "price_usd": 0.08,
            "price_atoms": "80000",
            "category": "intelligence",
            "trial_free": 2,
            "description": "Track whale wallets across multiple blockchains simultaneously — maps cross-chain capital flows, bridge migrations, and multi-network positioning of top holders",
        },
    }
    TOOL_PRICES.update(_NEW_TOOL_PRICES)
except Exception as e:
    logger.warning(f"x402 enforcement: could not add new tool prices: {e}")

# ── TOOL_PRICES lazy loading (deferred — saves ~1.5s cold start) ──
_tool_prices_extras_loaded = False


def _ensure_tool_prices():
    """Load expanded tools, bundles, and DataBus pricing on first use."""
    global _tool_prices_extras_loaded
    if _tool_prices_extras_loaded:
        return
    _tool_prices_extras_loaded = True

    # Expanded tools: 44 new specialized tools + 80 per-chain variants
    try:
        from app.routers._expanded_tools import ADDITIONAL_TOOLS as _EXPANDED_TOOLS

        TOOL_PRICES.update(_EXPANDED_TOOLS)
        logger.info(f"x402 enforcement: loaded {len(_EXPANDED_TOOLS)} expanded tools")
    except Exception as e:
        logger.warning(f"x402 enforcement: could not load expanded tools: {e}")

    # x402_tools.py BUNDLES dict as fallback
    try:
        from app.routers.x402_tools import BUNDLES as _TOOLS_BUNDLES

        for bid, b in _TOOLS_BUNDLES.items():
            if bid not in TOOL_PRICES and b.get("category") == "bundle":
                TOOL_PRICES[bid] = {
                    "price_usd": b.get("bundle_price_usd", 0.01),
                    "price_atoms": b.get("bundle_price_atoms", "10000"),
                    "category": "bundle",
                    "trial_free": b.get("trial_free", 1),
                    "description": b.get("name", bid),
                }
    except Exception as e:
        logger.warning(f"x402 enforcement: could not load x402_tools bundles: {e}")

    # Clean up corrupted tool IDs
    _bad_keys = [k for k in TOOL_PRICES if "{" in k or "/" in k or " " in k]
    if _bad_keys:
        logger.warning(f"Removing corrupted tool IDs: {_bad_keys}")
    for _k in _bad_keys:
        del TOOL_PRICES[_k]


# ── Load all tool prices from gateway configs ──
_load_tool_prices()

# ── Clean up corrupted tool IDs (template variables, paths with slashes/braces) ──
# (bad-key cleanup moved into _ensure_tool_prices())

# ── Chain display names (used in human-readable messages) ──

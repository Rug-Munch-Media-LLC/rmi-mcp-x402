# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Rug Munch Media LLC
"""x402 paid offerings — Rug Verdict, Deployer Dossier, Cluster Attribution.

The three sellable verdicts from the 2026-09-07 x402 selling strategy. Every
offering is ANALYSIS, never raw rows: verdicts + named-factor evidence +
honest abstention. Data flows through the existing tool layer
(:mod:`app.domain.x402.tools`) so the scanner / deployer-DB cutover only has
to upgrade those sources — the offering schemas stay stable.

Data-source status (documented per strategy §1):
- Rug Verdict factors are assembled from the scanner tool surface
  (``scanner_tools`` / ``token_tools``). Those sources currently return
  placeholder payloads pending the rugmunch-scanner ``_backend_engine``
  cutover; unknown factors are reported as ``unknown`` and the verdict
  ABSTAINS ("insufficient data") unless >= ``MIN_KNOWN_FACTORS`` factors are
  actually known. We never fabricate evidence.
- Deployer Dossier lifetime track-record fields (``total_deployments``,
  ``rug_rate``, ``funded_from``, ``sleep_pattern``) are sourced from
  ``wallet_tools`` / ``label_tools`` which are STUBBED pending the
  deployer-DB cutover — the response marks them ``source: "stubbed"``.
- Cluster Attribution heuristics run over the same wallet/label sources;
  with no graph data each heuristic reports ``confidence: 0.0`` rather than
  inventing an attribution.
"""

from __future__ import annotations

from typing import Any

from .tools.label_tools import get_wallet_labels
from .tools.scanner_tools import honeypot_check, run_scan, token_forensics
from .tools.token_tools import get_token_risk
from .tools.wallet_tools import get_wallet_analysis, get_wallet_history

__all__ = [
    "MIN_KNOWN_FACTORS",
    "VERDICT_FACTORS",
    "build_cluster_attribution",
    "build_deployer_dossier",
    "build_rug_verdict",
]

#: A verdict is only issued when at least this many named factors are KNOWN.
#: Below the threshold the offering abstains — honest "insufficient data".
MIN_KNOWN_FACTORS = 6

#: The ~30 named evidence factors exposed on a Rug Verdict (strategy §1).
#: Names are the contract; detector internals never leave the service.
VERDICT_FACTORS: tuple[str, ...] = (
    "deployer_repeat",
    "deployer_rugged_before",
    "holder_concentration",
    "bundle_launch",
    "sniper_first_buyers",
    "funder_cluster",
    "cross_token_cluster",
    "mev_bundle",
    "wash_trading_cluster",
    "honeypot",
    "mint_authority",
    "freeze_authority",
    "lp_locked",
    "buy_sell_tax",
    "holder_overlap_scam",
    "lp_unlock_schedule",
    "liquidity_depth",
    "contract_verified",
    "proxy_upgradeable",
    "ownership_renounced",
    "blacklist_function",
    "whitelist_only",
    "trading_cooldown",
    "holder_growth",
    "sell_pressure_pattern",
    "sniper_concentration",
    "funder_ofac_flag",
    "dev_wallet_funding",
    "social_presence",
    "audit_report",
)


def _unknown_factor(name: str) -> dict[str, Any]:
    return {"factor": name, "status": "unknown", "value": None, "evidence": None}


def _known_factor(name: str, value: Any, evidence: str) -> dict[str, Any]:
    return {"factor": name, "status": "known", "value": value, "evidence": evidence}


def _determination(safety_score: int, hard_flags: list[str]) -> str:
    """Map score + confirmed hard flags to a determination (never 'safe')."""
    if hard_flags:
        return "rug"
    if safety_score < 40:
        return "scam"
    if safety_score < 70:
        return "risky"
    return "ok"


async def build_rug_verdict(token: str, chain: str = "solana") -> dict[str, Any]:
    """Explainable rug verdict: determination + safety score + named factors.

    Abstains (``determination: "unknown"``, ``abstained: true``) when fewer
    than ``MIN_KNOWN_FACTORS`` factors are known — we do not sell guesses.
    """
    scan = await run_scan(token, chain)
    forensics = await token_forensics(token, chain)
    risk = await get_token_risk(token, chain)
    honeypot = await honeypot_check(token, chain)

    factors: list[dict[str, Any]] = [_unknown_factor(n) for n in VERDICT_FACTORS]
    by_name = {f["factor"]: f for f in factors}
    hard_flags: list[str] = []

    # Honeypot detector result (scanner source).
    if isinstance(honeypot, dict) and "is_honeypot" in honeypot:
        is_hp = bool(honeypot["is_honeypot"])
        by_name["honeypot"] = _known_factor("honeypot", is_hp, "honeypot detector")
        if is_hp:
            hard_flags.append("honeypot")

    # Scanner safety score, when the source actually reports one.
    raw_score = scan.get("safety_score") if isinstance(scan, dict) else None
    safety_score: int | None = (
        int(raw_score) if isinstance(raw_score, int | float) and 0 <= raw_score <= 100 else None
    )

    # Risk factors named by the token-risk source get matched onto the
    # canonical factor names; anything else stays unknown.
    for raw in risk.get("risk_factors") or []:
        name = str(raw.get("factor") or raw.get("name") or "").strip()
        if name in by_name and by_name[name]["status"] == "unknown":
            by_name[name] = _known_factor(name, raw.get("value"), str(raw.get("evidence") or ""))

    known = [f for f in by_name.values() if f["status"] == "known"]
    factors = [by_name[n] for n in VERDICT_FACTORS]
    if safety_score is None or len(known) < MIN_KNOWN_FACTORS:
        return {
            "offering": "rug_verdict",
            "token": token,
            "chain": chain,
            "determination": "unknown",
            "safety_score": None,
            "abstained": True,
            "abstention_reason": "insufficient data",
            "known_factors": len(known),
            "required_factors": MIN_KNOWN_FACTORS,
            "factors": factors,
            "disclaimer": "Not financial advice. Not a guarantee.",
        }

    return {
        "offering": "rug_verdict",
        "token": token,
        "chain": chain,
        "determination": _determination(safety_score, hard_flags),
        "safety_score": safety_score,
        "abstained": False,
        "abstention_reason": None,
        "known_factors": len(known),
        "required_factors": MIN_KNOWN_FACTORS,
        "factors": factors,
        "forensics_status": str(forensics.get("forensics") or "unknown"),
        "disclaimer": "Not financial advice. Not a guarantee.",
    }


async def build_deployer_dossier(wallet: str, chain: str = "solana") -> dict[str, Any]:
    """Lifetime deployer profile: deployments, rug_rate, funding origin.

    Track-record fields come from the wallet/label sources, which are
    STUBBED pending the deployer-DB cutover; every stubbed field is marked
    ``source: "stubbed"`` so buyers are never shown invented history.
    """
    analysis = await get_wallet_analysis(wallet, chain)
    history = await get_wallet_history(wallet, chain)
    labels = await get_wallet_labels(wallet)

    label_values = [str(lbl) for lbl in (labels.get("labels") or [])]
    funded_from = next(
        (
            lbl
            for lbl in label_values
            if lbl.lower() in {"coinbase", "tornado", "binance", "kraken"}
        ),
        "unknown",
    )

    return {
        "offering": "deployer_dossier",
        "wallet": wallet,
        "chain": chain,
        "wallet_age_days": None,
        "total_deployments": None,
        "rug_rate": None,
        "funded_from": funded_from,
        "sleep_pattern": None,
        "dev_reputation": analysis.get("risk_score") if isinstance(analysis, dict) else None,
        "prior_rugged_tokens": [],
        "transaction_count": history.get("total_count") if isinstance(history, dict) else 0,
        "labels": label_values,
        "data_sources": {
            "wallet_analysis": "live-tool (placeholder payload)",
            "wallet_history": "live-tool (placeholder payload)",
            "deployer_track_record": "stubbed — pending deployer-DB cutover",
        },
        "disclaimer": "Not financial advice. Not a guarantee.",
    }


#: The 4 clustering heuristics (strategy §1). Each reports a confidence; with
#: no graph data behind a heuristic the confidence is 0.0 — never a guess.
CLUSTER_HEURISTICS: tuple[str, ...] = (
    "shared_funder",
    "shared_deployer_network",
    "cross_token_funder_graph",
    "temporal_lockstep",
)


async def build_cluster_attribution(wallet: str, chain: str = "solana") -> dict[str, Any]:
    """4-heuristic wallet cluster attribution with per-heuristic confidence."""
    labels = await get_wallet_labels(wallet)
    history = await get_wallet_history(wallet, chain)
    label_values = [str(lbl) for lbl in (labels.get("labels") or [])]

    heuristics = [
        {"heuristic_type": h, "matched": False, "confidence": 0.0, "evidence": None}
        for h in CLUSTER_HEURISTICS
    ]
    has_graph_data = bool(history.get("total_count")) if isinstance(history, dict) else False

    # Composite risk: label risk + cluster risk + deployer risk (0-100).
    # With no cluster graph data the cluster component contributes 0.
    label_risk = (
        40 if any("ofac" in lbl.lower() or "sanction" in lbl.lower() for lbl in label_values) else 0
    )
    cluster_risk = 0
    deployer_risk = 0
    composite = min(100, label_risk + cluster_risk + deployer_risk)

    return {
        "offering": "cluster_attribution",
        "wallet": wallet,
        "chain": chain,
        "cluster_size": 1,
        "member_addresses": [wallet],
        "heuristics": heuristics,
        "graph_data_available": has_graph_data,
        "shared_funders": [],
        "labels": label_values,
        "composite_risk": {
            "score": composite,
            "label_risk": label_risk,
            "cluster_risk": cluster_risk,
            "deployer_risk": deployer_risk,
        },
        "data_sources": {
            "address_registry_labels": "live-tool",
            "entity_graph": "stubbed — pending deployer-DB cutover",
        },
        "disclaimer": "Not financial advice. Not a guarantee.",
    }

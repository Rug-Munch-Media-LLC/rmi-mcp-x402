"""Rug Munch Verified Badge — API routes.

Public + private endpoints for the verification program.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.domain.verified.models import (
    TokenApplication,
    VerificationResult,
    VerificationStatus,
    VerifiedToken,
)
from app.domain.verified.service import VerifiedBadgeService

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/verified", tags=["verified-badge"])


# ── Response Models ────────────────────────────────────────

class SubmitApplicationResponse(BaseModel):
    """Response after submitting an application."""

    application_id: str
    status: VerificationStatus
    estimated_completion_days: int = 14
    message: str = "Application received. Review in progress."
    next_steps: list[str] = Field(default_factory=list)


class VerifiedTokenPublic(BaseModel):
    """Public-facing view of a verified token."""

    token_name: str
    token_symbol: str
    chain: str
    contract_address: str
    status: VerificationStatus
    verified_at: datetime | None
    badge_expires_at: datetime | None
    public_summary: str
    public_risks: list[str]
    public_strengths: list[str]
    overall_score: float
    project_website: str = ""


class VerifiedTokenList(BaseModel):
    """List of verified tokens for the public registry."""

    total: int
    verified: int
    verified_with_caveats: int
    revoked: int
    tokens: list[VerifiedTokenPublic]


# ── Application Endpoints ─────────────────────────────────

@router.post("/apply", response_model=SubmitApplicationResponse)
async def submit_application(
    application: TokenApplication,
    background_tasks: BackgroundTasks,
) -> SubmitApplicationResponse:
    """Submit a token for Rug Munch Verified badge review.

    Public endpoint. The development team submits their application
    with full disclosures. Our team reviews within 7-14 business days.

    **MANDATORY DISCLOSURES:**
    - Full team member list with wallet addresses
    - All treasury and team wallets
    - Complete tokenomics
    - Audit reports (if any)
    - Whitepaper and roadmap

    By submitting, you agree to the Terms of Service including the
    ABSOLUTE HONESTY POLICY.
    """
    service = VerifiedBadgeService()

    try:
        result = await service.submit_application(application)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    log.info(
        "verified_application_received token=%s chain=%s",
        application.token_symbol,
        application.chain,
    )

    return SubmitApplicationResponse(
        application_id=f"{application.chain}:{application.contract_address}",
        status=result.status,
        estimated_completion_days=VerifiedBadgeService.REVIEW_TARGET_DAYS,
        message=(
            "Application received. Our security team will review all "
            "disclosures, run all 32 detectors, and produce an honest "
            f"assessment within {VerifiedBadgeService.REVIEW_TARGET_DAYS} "
            "business days."
        ),
        next_steps=[
            "1. Our team will review your disclosures within 3-5 business days",
            "2. We may contact you with follow-up questions",
            "3. Full 32-detector scan will be run",
            "4. Manual code review by our security team",
            "5. Deployer and team wallet history analysis",
            "6. Final honest report delivered to you",
            "7. If verified, badge issued + public listing",
        ],
    )


@router.get("/status/{chain}/{address}")
async def get_verification_status(
    chain: str, address: str
) -> dict[str, Any]:
    """Check the verification status of a token.

    Public endpoint. Anyone can check if a token is verified and see
    its public summary.
    """
    # In production, this queries the database
    # For now, return a placeholder
    return {
        "chain": chain,
        "address": address,
        "status": "not_found",
        "message": "Token not found in verification registry",
    }


@router.get("/check/{chain}/{address}")
async def check_token_verified(
    chain: str, address: str
) -> dict[str, Any]:
    """Quick check if a token is currently verified.

    Returns:
        is_verified: bool
        status: current VerificationStatus
        verified_at: ISO timestamp if verified
        badge_expires_at: ISO timestamp if verified
    """
    return {
        "chain": chain,
        "address": address,
        "is_verified": False,
        "status": "unknown",
    }


# ── Public Registry ───────────────────────────────────────

@router.get("/registry", response_model=VerifiedTokenList)
async def get_verified_registry(
    chain: str | None = Query(None, description="Filter by chain"),
    status: VerificationStatus | None = Query(None, description="Filter by status"),
    search: str | None = Query(None, description="Search by name/symbol"),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
) -> VerifiedTokenList:
    """Get the public registry of verified tokens.

    Shows ALL tokens that have gone through the verification process,
    including verified, verified with caveats, AND revoked. This is
    critical for transparency.
    """
    return VerifiedTokenList(
        total=0,
        verified=0,
        verified_with_caveats=0,
        revoked=0,
        tokens=[],
    )


@router.get("/registry/{slug}", response_model=VerifiedTokenPublic)
async def get_verified_token_by_slug(
    slug: str
) -> VerifiedTokenPublic:
    """Get a specific verified token by its public slug.

    Public endpoint. Shows the full verification report for a token.
    """
    raise HTTPException(
        status_code=404,
        detail=f"Verified token with slug '{slug}' not found",
    )


# ── MCP Integration ───────────────────────────────────────

@router.get("/mcp/check/{chain}/{address}")
async def mcp_check_verified(
    chain: str, address: str
) -> dict[str, Any]:
    """MCP endpoint: Check if a token is verified.

    Returns the is_verified field that MCP tools include in their responses.
    AI agents (Claude Code, Cursor, opencode) can call this to check
    if a token has been independently verified by Rug Munch.

    Returns:
        is_verified: bool — Whether the token has the Rug Munch Verified badge
        status: str — Current badge status (verified, revoked, expired, none)
        verified_at: str | None — ISO timestamp when badge was issued
        badge_expires_at: str | None — ISO timestamp when badge expires
        public_url: str | None — URL to the public verification report
    """
    return {
        "chain": chain,
        "address": address,
        "is_verified": False,
        "status": "none",
        "verified_at": None,
        "badge_expires_at": None,
        "public_url": None,
    }


# ── Internal Admin ────────────────────────────────────────

@router.post("/admin/revoke/{chain}/{address}")
async def admin_revoke_token(
    chain: str, address: str, reason: str, evidence: str
) -> dict[str, Any]:
    """Admin endpoint: Revoke a verified token's badge.

    Requires admin auth. Used when:
    - New information emerges contradicting original assessment
    - Team withheld material information
    - Contract is upgraded with suspicious functionality
    - Deployer wallet engages in suspicious activity
    - Monitoring detects critical risk

    The revocation is immediate and public.
    """
    log.warning(
        "verified_badge_admin_revoke chain=%s address=%s reason=%s",
        chain, address, reason,
    )
    return {
        "chain": chain,
        "address": address,
        "status": "revoked",
        "revoked_at": datetime.now(UTC).isoformat(),
        "reason": reason,
        "evidence": evidence,
        "public_disclosure": "Revocation notice will be published within 24 hours.",
    }


@router.post("/admin/renew/{chain}/{address}")
async def admin_renew_token(
    chain: str, address: str
) -> dict[str, Any]:
    """Admin endpoint: Renew a verified token's annual badge."""
    return {
        "chain": chain,
        "address": address,
        "status": "renewed",
        "new_expiry": (datetime.now(UTC).timestamp() + 365 * 24 * 3600),
    }

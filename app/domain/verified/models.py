"""Rug Munch Verified Badge — domain models.

Data models for the token verification program. When a project applies
for the Rug Munch Verified badge, we run all 32 detectors + manual
review. The result is stored in the verified_tokens table.
"""
from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class VerificationStatus(str, Enum):
    """Current status of a verified token."""

    PENDING = "pending"  # Application submitted, review in progress
    UNDER_REVIEW = "under_review"  # Active review by our team
    VERIFIED = "verified"  # Passed all checks, badge issued
    VERIFIED_CAVEATS = "verified_caveats"  # Passed with documented concerns
    REJECTED = "rejected"  # Did not pass
    REVOKED = "revoked"  # Was verified, now revoked
    EXPIRED = "expired"  # Annual re-verification not completed


class FindingSeverity(str, Enum):
    """Severity of a verification finding."""

    CRITICAL = "critical"  # Must be fixed, blocks verification
    HIGH = "high"  # Should be addressed, may block with caveats
    MEDIUM = "medium"  # Notable, does not block
    LOW = "low"  # Informational
    INFO = "info"  # Context only


class DetectorResult(BaseModel):
    """Result from a single detector scan."""

    detector_name: str
    category: str
    severity: FindingSeverity
    passed: bool
    score: float = Field(ge=0, le=100)
    evidence: str
    raw_output: dict[str, Any] = Field(default_factory=dict)
    scanned_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ManualFinding(BaseModel):
    """A finding from manual code/team review by our analysts."""

    finding_id: str
    category: str  # e.g., "code_review", "team_history", "tokenomics"
    severity: FindingSeverity
    title: str
    description: str
    evidence: str
    recommendation: str
    found_by: str  # Analyst name
    found_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TeamMemberDisclosure(BaseModel):
    """Mandatory disclosure for a team member."""

    legal_name: str
    role: str
    wallet_addresses: dict[str, list[str]]  # chain -> list of addresses
    prior_projects: list[str] = Field(default_factory=list)
    background: str = ""
    country: str = ""


class TokenApplication(BaseModel):
    """Application submitted by a token team for verification."""

    # Token info
    token_name: str
    token_symbol: str
    chain: str
    contract_address: str
    decimals: int = 18

    # Team disclosures (mandatory)
    team_members: list[TeamMemberDisclosure]
    all_team_wallets: dict[str, list[str]]  # chain -> addresses
    tokenomics_doc: str
    audit_reports: list[str] = Field(default_factory=list)
    whitepaper_url: str = ""
    roadmap_url: str = ""

    # Financial disclosures
    initial_market_cap: float | None = None
    liquidity_provision: str = ""
    presale_info: str = ""
    treasury_wallets: dict[str, list[str]] = Field(default_factory=dict)
    unlock_schedule: str = ""

    # Contact
    primary_contact: str  # email
    telegram: str = ""
    project_website: str = ""

    # Submission metadata
    submitted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    submitted_by: str  # wallet or email of submitter


class VerificationResult(BaseModel):
    """Complete result of a verification review."""

    # What was reviewed
    application: TokenApplication
    scan_results: list[DetectorResult] = Field(default_factory=list)
    manual_findings: list[ManualFinding] = Field(default_factory=list)

    # Summary
    total_detectors_run: int = 0
    detectors_passed: int = 0
    detectors_failed: int = 0
    critical_findings: int = 0
    high_findings: int = 0
    medium_findings: int = 0
    low_findings: int = 0

    # Verdict
    status: VerificationStatus = VerificationStatus.UNDER_REVIEW
    verdict_summary: str = ""
    overall_score: float = Field(ge=0, le=100)

    # Metadata
    reviewed_by: list[str] = Field(default_factory=list)  # Analyst names
    reviewed_at: datetime | None = None
    report_url: str = ""  # Internal full report

    # Public summary (shown on rugmunch.io/verified)
    public_summary: str = ""
    public_risks: list[str] = Field(default_factory=list)
    public_strengths: list[str] = Field(default_factory=list)


class VerifiedToken(BaseModel):
    """A token that has been verified (or is in the process)."""

    contract_address: str
    chain: str
    token_name: str
    token_symbol: str

    # Current status
    status: VerificationStatus
    verified_at: datetime | None = None
    last_monitored_at: datetime | None = None
    badge_expires_at: datetime | None = None

    # Latest verification
    latest_result: VerificationResult | None = None

    # Public info
    is_public: bool = True  # Show in public registry
    public_slug: str  # rugmunch.io/verified/{slug}
    project_website: str = ""
    project_twitter: str = ""

    # Internal
    internal_notes: str = ""
    contact_email: str = ""
    monthly_monitoring: bool = False
    monthly_fee_paid: bool = False
    last_payment_at: datetime | None = None

    # Audit trail
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    revoked_at: datetime | None = None
    revoked_reason: str = ""

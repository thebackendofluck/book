# Companion code for "The Backend of Luck" - Chapter 33, Operational Playbooks.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Shared Pydantic models for AcmetoCasino Backoffice Admin Platform.
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class AdminRole(str, enum.Enum):
    SUPER_ADMIN = "super_admin"
    COMPLIANCE = "compliance"
    FINANCE = "finance"
    CS = "cs"
    MARKETING = "marketing"
    READ_ONLY = "read_only"


class KYCStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    NOT_SUBMITTED = "not_submitted"


class WithdrawalStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class PlayerStatus(str, enum.Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    SELF_EXCLUDED = "self_excluded"
    DORMANT = "dormant"
    CLOSED = "closed"
    PENDING_VERIFICATION = "pending_verification"


class Jurisdiction(str, enum.Enum):
    UKGC = "UKGC"
    MGA = "MGA"
    DGE = "DGE"
    KAHNAWAKE = "KAHNAWAKE"
    CURACAO = "CURACAO"


class RGTriggerType(str, enum.Enum):
    SPEND_VELOCITY = "spend_velocity"
    SESSION_LENGTH = "session_length"
    LOSS_CHASING = "loss_chasing"
    FAILED_AFFORDABILITY = "failed_affordability"
    SELF_REPORTED = "self_reported"
    CUSTOMER_CONTACT = "customer_contact"


class BonusType(str, enum.Enum):
    WELCOME = "welcome"
    RELOAD = "reload"
    FREE_SPINS = "free_spins"
    CASHBACK = "cashback"
    LOYALTY = "loyalty"
    VIP = "vip"
    RETENTION = "retention"


# ---------------------------------------------------------------------------
# Player models
# ---------------------------------------------------------------------------

class PlayerBase(BaseModel):
    player_id: str
    username: str
    email: EmailStr
    first_name: str
    last_name: str
    date_of_birth: str
    country_code: str
    currency: str = "GBP"
    jurisdiction: Jurisdiction = Jurisdiction.UKGC
    status: PlayerStatus = PlayerStatus.ACTIVE
    brand: str = "AcmetoCasino"


class PlayerSummary(PlayerBase):
    registered_at: datetime
    last_login: Optional[datetime] = None
    kyc_status: KYCStatus = KYCStatus.NOT_SUBMITTED
    total_deposits: float = 0.0
    total_withdrawals: float = 0.0
    ggr_lifetime: float = 0.0
    balance: float = 0.0
    bonus_balance: float = 0.0
    tags: List[str] = Field(default_factory=list)


class PlayerDetail(PlayerSummary):
    phone: Optional[str] = None
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    city: Optional[str] = None
    postcode: Optional[str] = None
    occupation: Optional[str] = None
    annual_income_band: Optional[str] = None
    deposit_limit_daily: Optional[float] = None
    deposit_limit_weekly: Optional[float] = None
    deposit_limit_monthly: Optional[float] = None
    marketing_email_opt_in: bool = False
    marketing_sms_opt_in: bool = False
    referral_source: Optional[str] = None
    affiliate_id: Optional[str] = None
    notes: List[str] = Field(default_factory=list)


class PlayerSearchRequest(BaseModel):
    query: Optional[str] = None
    email: Optional[str] = None
    player_id: Optional[str] = None
    status: Optional[PlayerStatus] = None
    kyc_status: Optional[KYCStatus] = None
    jurisdiction: Optional[Jurisdiction] = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=25, ge=1, le=100)


class PlayerSearchResponse(BaseModel):
    players: List[PlayerSummary]
    total: int
    page: int
    page_size: int
    total_pages: int


# ---------------------------------------------------------------------------
# KYC models
# ---------------------------------------------------------------------------

class KYCDocument(BaseModel):
    document_id: str
    player_id: str
    document_type: str  # passport, driving_licence, utility_bill, bank_statement
    submitted_at: datetime
    reviewed_at: Optional[datetime] = None
    reviewed_by: Optional[str] = None
    status: KYCStatus = KYCStatus.PENDING
    rejection_reason: Optional[str] = None
    expiry_date: Optional[str] = None
    file_url: str


class KYCReviewRequest(BaseModel):
    document_id: str
    action: KYCStatus  # APPROVED or REJECTED
    rejection_reason: Optional[str] = None
    reviewer_notes: Optional[str] = None


# ---------------------------------------------------------------------------
# Affordability models
# ---------------------------------------------------------------------------

class AffordabilityCheck(BaseModel):
    check_id: str
    player_id: str
    checked_at: datetime
    period_days: int = 90
    total_deposits: float
    total_losses: float
    stated_annual_income: Optional[float] = None
    income_band: Optional[str] = None
    affordability_ratio: float  # losses / annual_income
    trigger_threshold: float = 0.3
    outcome: str  # pass / fail / review
    action_taken: Optional[str] = None
    reviewed_by: Optional[str] = None


# ---------------------------------------------------------------------------
# Compliance models
# ---------------------------------------------------------------------------

class RegulatoryReport(BaseModel):
    report_id: str
    jurisdiction: Jurisdiction
    report_type: str
    period_start: datetime
    period_end: datetime
    generated_at: datetime
    generated_by: str
    status: str = "draft"  # draft / submitted / accepted
    file_path: Optional[str] = None
    submission_reference: Optional[str] = None


class SOWRecord(BaseModel):
    sow_id: str
    player_id: str
    requested_at: datetime
    deadline: datetime
    submitted_at: Optional[datetime] = None
    reviewed_at: Optional[datetime] = None
    outcome: Optional[str] = None  # accepted / rejected / pending
    documents: List[str] = Field(default_factory=list)
    notes: Optional[str] = None
    reviewed_by: Optional[str] = None


class RGAuditEntry(BaseModel):
    audit_id: str
    player_id: str
    trigger_type: RGTriggerType
    triggered_at: datetime
    triggered_by: str  # system / agent_id
    action_taken: str
    outcome: Optional[str] = None
    follow_up_required: bool = False
    follow_up_due: Optional[datetime] = None
    resolved_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Finance models
# ---------------------------------------------------------------------------

class WithdrawalRequest(BaseModel):
    withdrawal_id: str
    player_id: str
    amount: float
    currency: str = "GBP"
    payment_method: str
    payment_reference: Optional[str] = None
    requested_at: datetime
    status: WithdrawalStatus = WithdrawalStatus.PENDING
    reviewed_at: Optional[datetime] = None
    reviewed_by: Optional[str] = None
    rejection_reason: Optional[str] = None
    kyc_verified: bool = False
    aml_cleared: bool = False


class WithdrawalDecisionRequest(BaseModel):
    withdrawal_id: str
    decision: WithdrawalStatus  # APPROVED or REJECTED
    rejection_reason: Optional[str] = None
    reviewer_notes: Optional[str] = None


class RevenueReport(BaseModel):
    report_id: str
    brand: str
    jurisdiction: Jurisdiction
    period_start: datetime
    period_end: datetime
    total_deposits: float
    total_withdrawals: float
    bonus_cost: float
    ggr: float  # gross gaming revenue
    ngr: float  # net gaming revenue after bonuses
    tax_rate: float
    tax_amount: float
    active_players: int
    new_players: int
    generated_at: datetime


# ---------------------------------------------------------------------------
# Security models
# ---------------------------------------------------------------------------

class AdminUser(BaseModel):
    admin_id: str
    username: str
    email: EmailStr
    role: AdminRole
    is_active: bool = True
    created_at: datetime
    last_login: Optional[datetime] = None
    allowed_ips: List[str] = Field(default_factory=list)
    two_fa_enabled: bool = True


class AuditLogEntry(BaseModel):
    log_id: str
    admin_id: str
    admin_username: str
    action: str
    resource_type: str
    resource_id: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)
    ip_address: str
    user_agent: Optional[str] = None
    timestamp: datetime
    outcome: str = "success"  # success / failure


class IPBlockEntry(BaseModel):
    entry_id: str
    ip_address: str
    cidr: Optional[str] = None
    list_type: str  # allowlist / blocklist
    reason: str
    created_by: str
    created_at: datetime
    expires_at: Optional[datetime] = None
    is_active: bool = True


# ---------------------------------------------------------------------------
# CRM models
# ---------------------------------------------------------------------------

class PlayerSegment(BaseModel):
    segment_id: str
    name: str
    description: Optional[str] = None
    criteria: Dict[str, Any] = Field(default_factory=dict)
    player_count: int = 0
    created_at: datetime
    updated_at: datetime
    created_by: str
    is_dynamic: bool = True


class Campaign(BaseModel):
    campaign_id: str
    name: str
    brand: str = "AcmetoCasino"
    segment_id: Optional[str] = None
    channel: str  # email / sms / push / in-app
    status: str = "draft"  # draft / scheduled / active / completed / cancelled
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    subject: Optional[str] = None
    body: Optional[str] = None
    created_by: str
    created_at: datetime
    sent_count: int = 0
    open_count: int = 0
    click_count: int = 0


class Bonus(BaseModel):
    bonus_id: str
    name: str
    bonus_type: BonusType
    brand: str = "AcmetoCasino"
    value: float
    currency: str = "GBP"
    wagering_requirement: float = 35.0
    max_bet: Optional[float] = None
    min_deposit: Optional[float] = None
    valid_days: int = 30
    is_active: bool = True
    jurisdiction: Optional[Jurisdiction] = None
    created_by: str
    created_at: datetime
    expiry_date: Optional[datetime] = None


class BonusAssignment(BaseModel):
    assignment_id: str
    bonus_id: str
    player_id: str
    assigned_by: str
    assigned_at: datetime
    expires_at: Optional[datetime] = None
    status: str = "active"  # active / used / expired / revoked
    wagered_amount: float = 0.0
    remaining_balance: float


# ---------------------------------------------------------------------------
# Dashboard models
# ---------------------------------------------------------------------------

class KPISnapshot(BaseModel):
    snapshot_at: datetime
    brand: str = "AcmetoCasino"
    active_players_today: int
    new_registrations_today: int
    deposits_today: float
    withdrawals_today: float
    ggr_today: float
    pending_kyc_reviews: int
    pending_withdrawals: int
    pending_rg_actions: int
    pending_sow_requests: int
    open_complaints: int


class AlertItem(BaseModel):
    alert_id: str
    alert_type: str  # kyc_review / withdrawal_hold / rg_trigger / sow_overdue
    priority: str = "medium"  # low / medium / high / critical
    player_id: Optional[str] = None
    message: str
    created_at: datetime
    assigned_to: Optional[str] = None
    is_resolved: bool = False


# ---------------------------------------------------------------------------
# Auth models
# ---------------------------------------------------------------------------

class TokenRequest(BaseModel):
    username: str
    password: str
    totp_code: Optional[str] = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 3600
    role: AdminRole


class TokenData(BaseModel):
    admin_id: str
    username: str
    role: AdminRole

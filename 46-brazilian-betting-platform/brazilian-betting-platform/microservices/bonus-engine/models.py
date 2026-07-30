# Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Domain models for the Bonus Engine.

Regulatory context:
  - Portaria MF 615/2023 (SPA) — free bet deductibility rules
  - SIGAP reporting requirements for bonus campaigns
  - Responsible gambling limits enforced via wagering requirements
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


# ── Enumerations ──────────────────────────────────────────────────────────────

class BonusStatus(str, Enum):
    ACTIVE    = "ACTIVE"
    COMPLETED = "COMPLETED"
    FORFEITED = "FORFEITED"
    EXPIRED   = "EXPIRED"
    PENDING   = "PENDING"


class BonusType(str, Enum):
    WELCOME         = "WELCOME"
    RELOAD          = "RELOAD"
    FREE_BET        = "FREE_BET"
    CASHBACK        = "CASHBACK"
    REFERRAL        = "REFERRAL"
    RESPONSIBLE_PLAY = "RESPONSIBLE_PLAY"


class CampaignStatus(str, Enum):
    DRAFT    = "DRAFT"
    ACTIVE   = "ACTIVE"
    PAUSED   = "PAUSED"
    ENDED    = "ENDED"


class WageringContribution(str, Enum):
    """Percentage a bet type contributes toward wagering clearance."""
    SPORTS_PRE_MATCH = "SPORTS_PRE_MATCH"    # 100%
    SPORTS_LIVE      = "SPORTS_LIVE"         # 80%
    CASINO_SLOTS     = "CASINO_SLOTS"        # 100%
    CASINO_TABLE     = "CASINO_TABLE"        # 20%
    LIVE_CASINO      = "LIVE_CASINO"         # 10%


# ── Campaign ──────────────────────────────────────────────────────────────────

class Campaign(BaseModel):
    campaign_id:    UUID          = Field(default_factory=uuid4)
    name:           str           = Field(min_length=3, max_length=120)
    bonus_type:     BonusType
    status:         CampaignStatus = CampaignStatus.DRAFT
    bonus_amount:   Decimal       = Field(gt=Decimal("0"))
    wagering_multiplier: int      = Field(ge=1, le=100, default=10)
    max_claims:     int           = Field(ge=1, default=1000)
    current_claims: int           = 0
    min_deposit:    Decimal       = Field(ge=Decimal("0"), default=Decimal("0"))
    eligible_cpfs:  list[str]    = Field(default_factory=list)
    # SIGAP fields (Portaria 615 Art. 12)
    sigap_deductible: bool        = False
    sigap_category:   str         = "BONUS_GERAL"
    valid_from:     datetime      = Field(default_factory=lambda: datetime.now(timezone.utc))
    valid_until:    datetime
    created_at:     datetime      = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata:       dict[str, Any] = Field(default_factory=dict)

    @field_validator("valid_until")
    @classmethod
    def valid_until_must_be_future(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v

    model_config = {"arbitrary_types_allowed": True}


# ── Wagering Requirement ──────────────────────────────────────────────────────

class WageringRequirement(BaseModel):
    requirement_id:      UUID    = Field(default_factory=uuid4)
    bonus_id:            UUID
    cpf:                 str
    total_required:      Decimal  # amount × multiplier
    total_wagered:       Decimal  = Decimal("0")
    remaining:           Decimal
    completed:           bool     = False
    created_at:          datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at:        datetime | None = None
    # Contribution tracking by bet type
    contributions:       dict[str, Decimal] = Field(default_factory=dict)
    # Settled bet ids already credited toward this requirement — replaying
    # the same settlement event must not double-count wagering progress.
    processed_bet_ids:   list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def compute_remaining(self) -> "WageringRequirement":
        self.remaining = max(Decimal("0"), self.total_required - self.total_wagered)
        if self.total_wagered >= self.total_required:
            self.completed = True
        return self


# ── Free Bet ──────────────────────────────────────────────────────────────────

class FreeBet(BaseModel):
    """
    Free bet tracking per Portaria 615/2023 Art. 18 — deductibility from GGR.

    Only free bets that meet the following criteria are deductible:
      - Awarded as part of a marketing campaign
      - Used within the validity window
      - Not associated with any prohibited bet types
    """
    free_bet_id:       UUID    = Field(default_factory=uuid4)
    bonus_id:          UUID
    cpf:               str
    face_value:        Decimal  = Field(gt=Decimal("0"))
    stake_replaced:    bool     = True    # true = stake returned if bet wins
    used:              bool     = False
    used_at:           datetime | None = None
    used_on_bet_id:    str | None = None
    outcome:           str | None = None  # WIN | LOSS | VOID
    net_player_win:    Decimal  = Decimal("0")
    sigap_reportable:  bool     = True
    deductible_from_ggr: bool   = False   # set to True after SIGAP validation
    created_at:        datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    valid_until:       datetime


# ── Bonus ─────────────────────────────────────────────────────────────────────

class Bonus(BaseModel):
    bonus_id:           UUID           = Field(default_factory=uuid4)
    campaign_id:        UUID
    cpf:                str
    status:             BonusStatus    = BonusStatus.PENDING
    bonus_type:         BonusType
    amount:             Decimal        = Field(gt=Decimal("0"))
    # Wagering
    wagering_multiplier: int           = Field(ge=1)
    wagering_requirement: WageringRequirement | None = None
    free_bet:           FreeBet | None = None
    # Lifecycle
    claimed_at:         datetime | None = None
    completed_at:       datetime | None = None
    forfeited_at:       datetime | None = None
    expired_at:         datetime | None = None
    valid_until:        datetime
    # SIGAP
    sigap_campaign_id:  str | None = None
    created_at:         datetime   = Field(default_factory=lambda: datetime.now(timezone.utc))
    # Anti-abuse signals captured at claim time (Portaria 615 multi-accounting
    # controls) — used to correlate claims across CPFs sharing a device/IP.
    device_id:          str | None = None
    ip_address:         str | None = None

    model_config = {"arbitrary_types_allowed": True}


# ── Request / Response schemas ────────────────────────────────────────────────

class CreateCampaignRequest(BaseModel):
    name:                str    = Field(min_length=3, max_length=120)
    bonus_type:          BonusType
    bonus_amount:        Decimal = Field(gt=Decimal("0"))
    wagering_multiplier: int    = Field(ge=1, le=100, default=10)
    max_claims:          int    = Field(ge=1, default=1000)
    min_deposit:         Decimal = Field(ge=Decimal("0"), default=Decimal("0"))
    valid_days:          int    = Field(ge=1, le=365, default=30)
    sigap_deductible:    bool   = False
    sigap_category:      str    = "BONUS_GERAL"


class ClaimBonusResponse(BaseModel):
    bonus_id:       str
    cpf:            str
    status:         str
    amount:         str
    wagering_required: str
    valid_until:    str
    message:        str


class WageringSettlementRequest(BaseModel):
    """Reference to a settled bet — wagering credit is always computed
    server-side from the settlement record this points to. The amount and
    bet type are deliberately NOT accepted here; a caller who could supply
    them directly could clear any rollover requirement with one request."""
    bet_id: str = Field(min_length=1)


class WageringCheckResponse(BaseModel):
    cpf:                str
    wagering_completed: bool
    total_required:     str
    total_wagered:      str
    remaining:          str
    percentage_done:    float


class SigapBonusRecord(BaseModel):
    """One row in the SIGAP free bet report (Portaria 615 Anexo III)."""
    report_period:     str      # YYYY-MM
    campaign_id:       str
    campaign_name:     str
    bonus_type:        str
    cpf:               str
    face_value:        str
    used:              bool
    deductible:        bool
    net_player_win:    str
    reported_at:       str


class SigapBonusReport(BaseModel):
    report_id:         str
    period:            str
    operator_cnpj:     str
    total_free_bets:   int
    total_face_value:  str
    total_deductible:  str
    records:           list[SigapBonusRecord]
    generated_at:      str

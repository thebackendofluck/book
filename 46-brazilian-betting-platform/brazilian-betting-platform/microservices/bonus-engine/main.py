# Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Bonus Engine — FastAPI application.

Endpoints:
  POST /bonuses/create                  — create a bonus campaign
  POST /bonuses/claim/{cpf}             — claim a bonus
  GET  /bonuses/{cpf}                   — active bonuses for player
  POST /bonuses/{id}/forfeit            — forfeit a bonus
  GET  /bonuses/sigap-report            — SIGAP free bet reporting
  POST /bonuses/wagering-check/{cpf}    — wagering requirement validation
  GET  /health                          — health check
"""

from __future__ import annotations

import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware

from auth import get_claims, require_cpf_access, require_operator, assert_cpf_or_operator
from models import (
    Bonus,
    BonusStatus,
    BonusType,
    Campaign,
    CampaignStatus,
    ClaimBonusResponse,
    CreateCampaignRequest,
    FreeBet,
    SigapBonusReport,
    WageringSettlementRequest,
    WageringCheckResponse,
    WageringRequirement,
)
from sigap_bonus import SigapBonusTracker
from verification import (
    DepositVerificationProvider,
    DepositVerificationUnavailableError,
    SettledBetNotFoundError,
    SettledBetOwnershipError,
    SettledBetProvider,
    get_deposit_provider,
    get_settled_bet_provider,
)
from wagering import WageringEngine

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format='{"time": "%(asctime)s", "level": "%(levelname)s", "msg": "%(message)s"}',
)
logger = logging.getLogger(__name__)

# ── App ───────────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info("Bonus Engine started")
    yield
    logger.info("Bonus Engine stopped")


app = FastAPI(
    title="Bonus Engine",
    description=(
        "Brazilian betting platform bonus management service. "
        "Compliant with Portaria MF 615/2023 and SIGAP reporting requirements."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# CORS is restricted to a configured allowlist — never "*" for an endpoint
# family that carries bearer tokens and player financial data.
_CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv("BONUS_ENGINE_CORS_ORIGINS", "https://apostas.acmetocasino.bet.br").split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

# ── In-memory stores (replace with DB in production) ─────────────────────────

_campaigns:    dict[str, Campaign]            = {}
_bonuses:      dict[str, Bonus]               = {}
_requirements: dict[str, WageringRequirement] = {}

wagering_engine = WageringEngine()
sigap_tracker   = SigapBonusTracker()

# Bonus types a player may hold at most once across their lifetime — a
# player must not be able to claim every WELCOME campaign in rotation.
ONE_PER_PLAYER_BONUS_TYPES = frozenset({BonusType.WELCOME})

# Claim statuses that count as "already used this offer" for dedup purposes.
# FORFEITED is included: claim -> immediately forfeit -> claim again would
# otherwise let a player replay a campaign's grant indefinitely.
_CLAIMED_STATUSES = (
    BonusStatus.ACTIVE,
    BonusStatus.PENDING,
    BonusStatus.COMPLETED,
    BonusStatus.FORFEITED,
)

# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status":     "UP",
        "service":    "bonus-engine",
        "version":    "1.0.0",
        "campaigns":  len(_campaigns),
        "bonuses":    len(_bonuses),
        "timestamp":  datetime.now(timezone.utc).isoformat(),
    }

# ── Campaigns ─────────────────────────────────────────────────────────────────

@app.post("/bonuses/create", status_code=201, dependencies=[Depends(require_operator)])
async def create_campaign(req: CreateCampaignRequest) -> Campaign:
    """Create a new bonus campaign. Operator/marketing role required."""
    now = datetime.now(timezone.utc)
    campaign = Campaign(
        name                = req.name,
        bonus_type          = req.bonus_type,
        bonus_amount        = req.bonus_amount,
        wagering_multiplier = req.wagering_multiplier,
        max_claims          = req.max_claims,
        min_deposit         = req.min_deposit,
        valid_from          = now,
        valid_until         = now + timedelta(days=req.valid_days),
        status              = CampaignStatus.ACTIVE,
        sigap_deductible    = req.sigap_deductible,
        sigap_category      = req.sigap_category,
    )
    _campaigns[str(campaign.campaign_id)] = campaign
    logger.info(
        f"Campaign created: {campaign.campaign_id} type={campaign.bonus_type} "
        f"amount={campaign.bonus_amount}"
    )
    return campaign

# ── Claim ─────────────────────────────────────────────────────────────────────

@app.post("/bonuses/claim/{cpf}", response_model=ClaimBonusResponse)
async def claim_bonus(
    cpf: str,
    request: Request,
    campaign_id: str = Query(...),
    device_id: str | None = Query(None, description="Client device fingerprint, if available"),
    claims: dict = Depends(require_cpf_access),
    deposits: DepositVerificationProvider = Depends(get_deposit_provider),
) -> ClaimBonusResponse:
    """Claim a bonus from an active campaign."""
    campaign = _campaigns.get(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    if campaign.status != CampaignStatus.ACTIVE:
        raise HTTPException(status_code=409, detail="Campaign is not active")

    now = datetime.now(timezone.utc)
    valid_until = campaign.valid_until
    if valid_until.tzinfo is None:
        valid_until = valid_until.replace(tzinfo=timezone.utc)
    if now > valid_until:
        raise HTTPException(status_code=409, detail="Campaign has expired")

    if campaign.current_claims >= campaign.max_claims:
        raise HTTPException(status_code=409, detail="Campaign claim limit reached")

    # Eligibility allowlist (dead code previously — never checked)
    if campaign.eligible_cpfs and cpf not in campaign.eligible_cpfs:
        raise HTTPException(status_code=403, detail="CPF not eligible for this campaign")

    # Duplicate-claim dedup: covers this campaign regardless of status
    # (including FORFEITED — claim -> forfeit -> claim must not repeat).
    existing_same_campaign = [
        b for b in _bonuses.values()
        if b.cpf == cpf
        and str(b.campaign_id) == campaign_id
        and b.status in _CLAIMED_STATUSES
    ]
    if existing_same_campaign:
        raise HTTPException(status_code=409, detail="Bonus already claimed for this campaign")

    # For one-per-player bonus types (e.g. WELCOME), also block claiming a
    # different campaign of the same type — otherwise a player could claim
    # every WELCOME campaign in rotation, one campaign_id at a time.
    if campaign.bonus_type in ONE_PER_PLAYER_BONUS_TYPES:
        existing_same_type = [
            b for b in _bonuses.values()
            if b.cpf == cpf
            and b.bonus_type == campaign.bonus_type
            and b.status in _CLAIMED_STATUSES
        ]
        if existing_same_type:
            raise HTTPException(
                status_code=409,
                detail=f"{campaign.bonus_type.value} bonus already claimed under a different campaign",
            )

    # Minimum deposit requirement (dead code previously — never checked
    # against a real deposit; verified server-side, never client-asserted).
    if campaign.min_deposit > Decimal("0"):
        try:
            total_deposits = await deposits.total_confirmed_deposits(cpf)
        except DepositVerificationUnavailableError as exc:
            raise HTTPException(
                status_code=503, detail="Unable to verify deposit eligibility"
            ) from exc
        if total_deposits < campaign.min_deposit:
            raise HTTPException(status_code=403, detail="Minimum deposit requirement not met")

    # Device/IP linkage — a device already used to claim this same campaign
    # under a different CPF is a strong multi-accounting signal.
    ip_address = request.client.host if request.client else None
    if device_id:
        colliding = [
            b for b in _bonuses.values()
            if b.device_id == device_id
            and b.cpf != cpf
            and str(b.campaign_id) == campaign_id
            and b.status in (BonusStatus.ACTIVE, BonusStatus.PENDING, BonusStatus.COMPLETED)
        ]
        if colliding:
            logger.warning(
                f"Bonus abuse signal: device_id={device_id} already used by "
                f"cpf={colliding[0].cpf} for campaign={campaign_id}"
            )
            raise HTTPException(status_code=409, detail="This device has already claimed this campaign")

    # One id shared by the bonus and its wagering requirement, so lookups by
    # bonus_id resolve the stored requirement (no orphaned copy).
    bonus_id = uuid.uuid4()

    # Create wagering requirement
    req_obj = wagering_engine.create_requirement(
        bonus_id            = bonus_id,
        cpf                 = cpf,
        bonus_amount        = campaign.bonus_amount,
        wagering_multiplier = campaign.wagering_multiplier,
    )

    # Create bonus
    bonus = Bonus(
        bonus_id            = bonus_id,
        campaign_id         = campaign.campaign_id,
        cpf                 = cpf,
        status              = BonusStatus.ACTIVE,
        bonus_type          = campaign.bonus_type,
        amount              = campaign.bonus_amount,
        wagering_multiplier = campaign.wagering_multiplier,
        wagering_requirement = req_obj,
        valid_until         = valid_until,
        claimed_at          = now,
        sigap_campaign_id   = campaign_id if campaign.sigap_deductible else None,
        device_id           = device_id,
        ip_address          = ip_address,
    )

    # Handle free bet type
    if campaign.bonus_type == BonusType.FREE_BET:
        fb = FreeBet(
            bonus_id    = bonus.bonus_id,
            cpf         = cpf,
            face_value  = campaign.bonus_amount,
            valid_until = valid_until,
        )
        bonus = bonus.model_copy(update={"free_bet": fb})
        sigap_tracker.register_free_bet(fb)

    _bonuses[str(bonus.bonus_id)] = bonus
    campaign.current_claims += 1
    _requirements[str(bonus_id)] = req_obj

    logger.info(
        f"Bonus claimed: id={bonus.bonus_id} cpf={cpf} "
        f"campaign={campaign_id} amount={bonus.amount}"
    )

    return ClaimBonusResponse(
        bonus_id          = str(bonus.bonus_id),
        cpf               = cpf,
        status            = bonus.status.value,
        amount            = str(bonus.amount),
        wagering_required = str(req_obj.total_required),
        valid_until       = valid_until.isoformat(),
        message           = "Bonus claimed successfully.",
    )

# ── List active bonuses ───────────────────────────────────────────────────────

@app.get("/bonuses/{cpf}", dependencies=[Depends(require_cpf_access)])
async def get_player_bonuses(cpf: str) -> list[dict[str, Any]]:
    """Return all active/pending bonuses for a player."""
    active = [
        b for b in _bonuses.values()
        if b.cpf == cpf and b.status in (BonusStatus.ACTIVE, BonusStatus.PENDING)
    ]
    return [
        {
            "bonus_id":     str(b.bonus_id),
            "campaign_id":  str(b.campaign_id),
            "type":         b.bonus_type.value,
            "status":       b.status.value,
            "amount":       str(b.amount),
            "valid_until":  b.valid_until.isoformat() if b.valid_until else None,
            "claimed_at":   b.claimed_at.isoformat() if b.claimed_at else None,
        }
        for b in active
    ]

# ── Forfeit ───────────────────────────────────────────────────────────────────

@app.post("/bonuses/{bonus_id}/forfeit")
async def forfeit_bonus(bonus_id: str, claims: dict = Depends(get_claims)) -> dict[str, str]:
    """Forfeit (cancel) an active bonus, removing any pending wagering balance."""
    bonus = _bonuses.get(bonus_id)
    if not bonus:
        raise HTTPException(status_code=404, detail="Bonus not found")

    # cpf isn't a path parameter here, so ownership is checked after load.
    assert_cpf_or_operator(claims, bonus.cpf)

    if bonus.status not in (BonusStatus.ACTIVE, BonusStatus.PENDING):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot forfeit bonus in status {bonus.status.value}"
        )

    now = datetime.now(timezone.utc)
    _bonuses[bonus_id] = bonus.model_copy(update={
        "status":      BonusStatus.FORFEITED,
        "forfeited_at": now,
    })
    logger.info(f"Bonus forfeited: id={bonus_id} cpf={bonus.cpf}")
    return {"bonus_id": bonus_id, "status": "FORFEITED", "forfeited_at": now.isoformat()}

# ── SIGAP report ──────────────────────────────────────────────────────────────

@app.get(
    "/bonuses/sigap-report",
    response_model=SigapBonusReport,
    dependencies=[Depends(require_operator)],
)
async def sigap_report(
    period: str = Query(..., description="Report period in YYYY-MM format", pattern=r"^\d{4}-\d{2}$")
) -> SigapBonusReport:
    """Generate SIGAP free bet deductibility report for a given month.
    Compliance/operator role required.

    Legal basis: Portaria MF 615/2023 Art. 18 — must be submitted by the
    10th of the following month.
    """
    logger.info(f"Generating SIGAP bonus report for period={period}")
    return sigap_tracker.generate_monthly_report(period)

# ── Wagering check ────────────────────────────────────────────────────────────

@app.post(
    "/bonuses/wagering-check/{cpf}",
    response_model=WageringCheckResponse,
    dependencies=[Depends(require_operator)],
)
async def wagering_check(
    cpf: str,
    req: WageringSettlementRequest,
    settled_bets: SettledBetProvider = Depends(get_settled_bet_provider),
) -> WageringCheckResponse:
    """Credit wagering progress from a settled bet.

    Called by the settlement/ledger service after a bet is settled
    (operator role required). The wager amount and bet type are resolved
    server-side from the settlement record `req.bet_id` points to — never
    accepted from the request body — so a caller cannot clear a bonus's
    rollover requirement by self-reporting amounts. Applying the same
    settled bet twice is a no-op (see WageringEngine.apply_wager).
    """
    # Find the active bonus requirement for this player
    active_bonus = next(
        (b for b in _bonuses.values()
         if b.cpf == cpf and b.status == BonusStatus.ACTIVE),
        None,
    )
    if not active_bonus or not active_bonus.wagering_requirement:
        raise HTTPException(
            status_code=404,
            detail="No active bonus with wagering requirement found for this CPF"
        )

    try:
        settled = await settled_bets.get_settled_bet(req.bet_id, cpf)
    except SettledBetOwnershipError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SettledBetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    req_key = str(active_bonus.wagering_requirement.bonus_id)
    requirement = _requirements.get(req_key) or active_bonus.wagering_requirement

    updated = wagering_engine.apply_wager(
        requirement  = requirement,
        wager_amount = settled.stake,
        bet_type     = settled.bet_type,
        bet_id       = settled.bet_id,
    )
    _requirements[req_key] = updated

    if updated.completed:
        _bonuses[str(active_bonus.bonus_id)] = active_bonus.model_copy(update={
            "status":       BonusStatus.COMPLETED,
            "completed_at": datetime.now(timezone.utc),
        })
        logger.info(f"Wagering completed for cpf={cpf} bonus={active_bonus.bonus_id}")

    return wagering_engine.build_response(cpf, updated)

# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8082"))
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        log_level="info",
        access_log=True,
    )

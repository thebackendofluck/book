# Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Responsible Gaming Service
===========================
FastAPI microservice for responsible gaming compliance under
Lei 14.790/2023 and SPA/MF Portaria 1231/2024.

Endpoints:
  POST   /limits/{cpf}               — set deposit / loss / session limits
  GET    /limits/{cpf}               — get current limits
  POST   /self-exclusion/{cpf}       — self-exclude (temporary or permanent)
  DELETE /self-exclusion/{cpf}       — revoke after cooling-off
  GET    /self-exclusion/check/{cpf} — check national registry
  POST   /alerts/{cpf}               — record behavioral risk alert
  GET    /reports/daily              — Portaria 1231 compliance report
  GET    /health                     — health check

All CPF path parameters are normalised before use (strips formatting).
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import structlog
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Path, Query, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_cpf_access  # noqa: F401
from database import (
    BehavioralAlertRecord,
    LimitRecord,
    SelfExclusionRecord as DBSelfExclusionRecord,
    create_tables,
    dispose_engine,
    get_session,
)
from limit_engine import LimitEngine, LimitExceededError, LimitIncreaseBlockedError
from models import (
    AlertType,
    BehavioralAlert,
    BehavioralAlertRequest,
    ComplianceReportRow,
    DailyReportResponse,
    LimitPeriod,
    LimitSetRequest,
    LimitType,
    PlayerLimit,
    PlayerLimitsResponse,
    RiskLevel,
    RiskScore,
    SelfExclusionCheckResponse,
    SelfExclusionRecord,
    SelfExclusionRequest,
    SelfExclusionResponse,
    SelfExclusionType,
)
from national_registry import (
    NationalRegistryClient,
    RevocationBlockedError,
)
from risk_scorer import RiskScorer

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ]
)
logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalise_cpf(cpf: str) -> str:
    return re.sub(r"\D", "", cpf)


def _cpf_to_hash(cpf: str) -> str:
    normalised = _normalise_cpf(cpf)
    return hashlib.sha256(normalised.encode("ascii")).hexdigest()


# ---------------------------------------------------------------------------
# Service singletons
# ---------------------------------------------------------------------------

_limit_engine = LimitEngine()
_registry_client = NationalRegistryClient()
_risk_scorer = RiskScorer()


# ---------------------------------------------------------------------------
# App lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_tables()
    logger.info("rg_service_started")
    yield
    await dispose_engine()
    logger.info("rg_service_shutdown")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Responsible Gaming Service",
    description=(
        "Deposit/loss limits, self-exclusion, behavioral risk scoring "
        "— Lei 14.790/2023 / Portaria 1231/2024"
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://apostas.acmetocasino.bet.br"],
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


# ---------------------------------------------------------------------------
# Limits endpoints
# ---------------------------------------------------------------------------


@app.post(
    "/limits/{cpf}",
    dependencies=[Depends(require_cpf_access)],
    response_model=PlayerLimit,
    status_code=status.HTTP_201_CREATED,
    summary="Set or update a deposit / loss / session limit",
)
async def set_limit(
    req: LimitSetRequest,
    cpf: str = Path(..., description="Player CPF"),
    session: AsyncSession = Depends(get_session),
) -> PlayerLimit:
    """
    Set a player's deposit, loss, or session limit.

    Decreases take effect immediately.
    Increases require a 24-hour cooling-off period (Portaria 615/2023 §12).
    """
    cpf_hash = _cpf_to_hash(cpf)
    now = datetime.now(timezone.utc)

    immediate, cooling_until = await _limit_engine.set_limit(
        cpf_hash,
        req.limit_type.value,
        req.period.value,
        req.amount,
    )

    # Persist to database
    limit_id = str(uuid.uuid4())
    resets_at: Optional[datetime] = None
    if req.period == LimitPeriod.DAILY:
        resets_at = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    elif req.period == LimitPeriod.WEEKLY:
        days_ahead = 7 - now.weekday()
        resets_at = (now + timedelta(days=days_ahead)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    elif req.period == LimitPeriod.MONTHLY:
        if now.month == 12:
            resets_at = now.replace(year=now.year + 1, month=1, day=1, hour=0, minute=0, second=0)
        else:
            resets_at = now.replace(month=now.month + 1, day=1, hour=0, minute=0, second=0)

    db_limit = LimitRecord(
        limit_id=limit_id,
        cpf_hash=cpf_hash,
        limit_type=req.limit_type.value,
        period=req.period.value,
        amount=req.amount,
        amount_used=0.0,
        is_active=True,
        set_at=now,
        resets_at=resets_at,
        updated_at=now,
    )
    session.add(db_limit)

    logger.info(
        "limit_set",
        cpf_hash=cpf_hash[:8],
        limit_type=req.limit_type.value,
        period=req.period.value,
        amount=req.amount,
        immediate=immediate,
    )

    return PlayerLimit(
        limit_id=limit_id,
        cpf_hash=cpf_hash,
        limit_type=req.limit_type,
        period=req.period,
        amount=req.amount,
        amount_used=0.0,
        amount_remaining=req.amount,
        set_at=now,
        resets_at=resets_at,
        is_active=True,
    )


@app.get(
    "/limits/{cpf}",
    dependencies=[Depends(require_cpf_access)],
    response_model=PlayerLimitsResponse,
    summary="Get all current limits for a player",
)
async def get_limits(
    cpf: str = Path(..., description="Player CPF"),
    session: AsyncSession = Depends(get_session),
) -> PlayerLimitsResponse:
    from sqlalchemy import select

    cpf_hash = _cpf_to_hash(cpf)
    result = await session.execute(
        select(LimitRecord).where(
            LimitRecord.cpf_hash == cpf_hash,
            LimitRecord.is_active.is_(True),
        )
    )
    db_limits = result.scalars().all()

    limits: List[PlayerLimit] = []
    for lim in db_limits:
        usage = await _limit_engine.get_usage(cpf_hash, lim.limit_type, lim.period)
        limits.append(
            PlayerLimit(
                limit_id=lim.limit_id,
                cpf_hash=cpf_hash,
                limit_type=LimitType(lim.limit_type),
                period=LimitPeriod(lim.period),
                amount=lim.amount,
                amount_used=usage,
                amount_remaining=max(0.0, lim.amount - usage),
                set_at=lim.set_at,
                resets_at=lim.resets_at,
                is_active=lim.is_active,
            )
        )

    return PlayerLimitsResponse(
        cpf_hash=cpf_hash,
        limits=limits,
        retrieved_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Self-exclusion endpoints
# ---------------------------------------------------------------------------


@app.post(
    "/self-exclusion/{cpf}",
    dependencies=[Depends(require_cpf_access)],
    response_model=SelfExclusionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Self-exclude a player (temporary or permanent)",
)
async def self_exclude(
    req: SelfExclusionRequest,
    cpf: str = Path(..., description="Player CPF"),
    session: AsyncSession = Depends(get_session),
) -> SelfExclusionResponse:
    """
    Register a player's self-exclusion.

    - Temporary: 1 day to 5 years.  Requires cooling-off before revocation.
    - Permanent: irrevocable.

    The exclusion is forwarded to the national Aposta Responsável registry
    if notify_national_registry is True.
    """
    cpf_hash = _cpf_to_hash(cpf)
    now = datetime.now(timezone.utc)
    exclusion_id = str(uuid.uuid4())

    ends_at: Optional[datetime] = None
    cooling_off_until: Optional[datetime] = None

    if req.exclusion_type == SelfExclusionType.TEMPORARY and req.duration_days:
        ends_at = now + timedelta(days=req.duration_days)
        # Cooling-off: cannot revoke during the exclusion period
        cooling_off_until = ends_at

    db_exclusion = DBSelfExclusionRecord(
        exclusion_id=exclusion_id,
        cpf_hash=cpf_hash,
        exclusion_type=req.exclusion_type.value,
        started_at=now,
        ends_at=ends_at,
        is_active=True,
        national_registry_notified=False,
        cooling_off_until=cooling_off_until,
        reason=req.reason,
    )
    session.add(db_exclusion)

    # Notify national registry
    if req.notify_national_registry:
        try:
            await _registry_client.register(
                cpf_hash,
                req.exclusion_type.value,
                req.duration_days,
            )
            db_exclusion.national_registry_notified = True
        except Exception as exc:
            logger.error(
                "national_registry_notification_failed",
                cpf_hash=cpf_hash[:8],
                error=str(exc),
            )

    logger.info(
        "self_exclusion_registered",
        cpf_hash=cpf_hash[:8],
        exclusion_type=req.exclusion_type.value,
        ends_at=ends_at.isoformat() if ends_at else "permanent",
    )

    record = SelfExclusionRecord(
        exclusion_id=exclusion_id,
        cpf_hash=cpf_hash,
        exclusion_type=req.exclusion_type,
        started_at=now,
        ends_at=ends_at,
        is_active=True,
        national_registry_notified=db_exclusion.national_registry_notified,
        cooling_off_until=cooling_off_until,
    )

    return SelfExclusionResponse(
        record=record,
        message=(
            f"Self-exclusion registered. "
            f"{'Permanent — cannot be revoked.' if req.exclusion_type == SelfExclusionType.PERMANENT else f'Ends at {ends_at.isoformat()}.'}"
        ),
    )


@app.delete(
    "/self-exclusion/{cpf}",
    dependencies=[Depends(require_cpf_access)],
    response_model=Dict[str, Any],
    summary="Revoke temporary self-exclusion after cooling-off",
)
async def revoke_self_exclusion(
    cpf: str = Path(..., description="Player CPF"),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    from sqlalchemy import select, update

    cpf_hash = _cpf_to_hash(cpf)
    now = datetime.now(timezone.utc)

    try:
        revoked = await _registry_client.revoke(cpf_hash)
    except RevocationBlockedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        )

    # Deactivate in DB
    result = await session.execute(
        select(DBSelfExclusionRecord).where(
            DBSelfExclusionRecord.cpf_hash == cpf_hash,
            DBSelfExclusionRecord.is_active.is_(True),
        )
    )
    records = result.scalars().all()
    for rec in records:
        rec.is_active = False

    logger.info("self_exclusion_revoked", cpf_hash=cpf_hash[:8])
    return {
        "cpf_hash": cpf_hash,
        "revoked": True,
        "revoked_at": now.isoformat(),
    }


@app.get(
    "/self-exclusion/check/{cpf}",
    response_model=SelfExclusionCheckResponse,
    summary="Check national self-exclusion registry for a CPF",
)
async def check_self_exclusion(
    cpf: str = Path(..., description="Player CPF"),
) -> SelfExclusionCheckResponse:
    cpf_hash = _cpf_to_hash(cpf)
    result = await _registry_client.check(cpf_hash)
    now = datetime.now(timezone.utc)

    exclusion_details: Optional[SelfExclusionRecord] = None
    if result.is_excluded and result.exclusion_id:
        exclusion_details = SelfExclusionRecord(
            exclusion_id=result.exclusion_id,
            cpf_hash=cpf_hash,
            exclusion_type=SelfExclusionType(result.exclusion_type or "permanent"),
            started_at=result.started_at or now,
            ends_at=result.ends_at,
            is_active=result.is_excluded,
        )

    return SelfExclusionCheckResponse(
        cpf_hash=cpf_hash,
        is_excluded=result.is_excluded,
        exclusion_details=exclusion_details,
        checked_at=now,
        sources=[result.source],
    )


# ---------------------------------------------------------------------------
# Behavioral alerts
# ---------------------------------------------------------------------------


@app.post(
    "/alerts/{cpf}",
    response_model=BehavioralAlert,
    status_code=status.HTTP_201_CREATED,
    summary="Record a behavioral risk alert for a player",
)
async def record_alert(
    req: BehavioralAlertRequest,
    cpf: str = Path(..., description="Player CPF"),
    session: AsyncSession = Depends(get_session),
) -> BehavioralAlert:
    cpf_hash = _cpf_to_hash(cpf)
    alert_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    db_alert = BehavioralAlertRecord(
        alert_id=alert_id,
        cpf_hash=cpf_hash,
        alert_type=req.alert_type.value,
        severity=req.severity.value,
        context_data=req.context,
        triggered_by=req.triggered_by,
        acknowledged=False,
        created_at=now,
    )
    session.add(db_alert)

    logger.info(
        "behavioral_alert",
        cpf_hash=cpf_hash[:8],
        alert_type=req.alert_type.value,
        severity=req.severity.value,
    )

    return BehavioralAlert(
        alert_id=alert_id,
        cpf_hash=cpf_hash,
        alert_type=req.alert_type,
        severity=req.severity,
        context=req.context,
        triggered_by=req.triggered_by,
        created_at=now,
        acknowledged=False,
    )


# ---------------------------------------------------------------------------
# Compliance reporting
# ---------------------------------------------------------------------------


@app.get(
    "/reports/daily",
    response_model=DailyReportResponse,
    summary="Portaria 1231/2024 daily compliance report",
)
async def daily_report(
    report_date: Optional[str] = Query(
        None, description="Date YYYY-MM-DD (defaults to today)", pattern=r"^\d{4}-\d{2}-\d{2}$"
    ),
    session: AsyncSession = Depends(get_session),
) -> DailyReportResponse:
    """
    Generate the daily compliance report required by Portaria SPA/MF 1231/2024.

    Aggregates:
      - Total registered players with limits
      - New self-exclusions
      - Permanent exclusions
      - High/critical risk player count
      - Total behavioral alerts triggered
      - Welfare-related blocks
    """
    from sqlalchemy import func, select

    now = datetime.now(timezone.utc)
    target_date = report_date or now.strftime("%Y-%m-%d")

    # Count active limits
    limits_result = await session.execute(
        select(func.count()).where(LimitRecord.is_active.is_(True))
    )
    active_limits = limits_result.scalar() or 0

    # Count unique players with limits
    players_result = await session.execute(
        select(func.count(LimitRecord.cpf_hash.distinct())).where(
            LimitRecord.is_active.is_(True)
        )
    )
    total_players = players_result.scalar() or 0

    # New self-exclusions today
    exclusions_result = await session.execute(
        select(func.count()).where(
            DBSelfExclusionRecord.is_active.is_(True),
        )
    )
    new_exclusions = exclusions_result.scalar() or 0

    # Permanent exclusions
    perm_result = await session.execute(
        select(func.count()).where(
            DBSelfExclusionRecord.exclusion_type == "permanent",
            DBSelfExclusionRecord.is_active.is_(True),
        )
    )
    permanent_exclusions = perm_result.scalar() or 0

    # Total alerts
    alerts_result = await session.execute(select(func.count(BehavioralAlertRecord.alert_id)))
    alerts_triggered = alerts_result.scalar() or 0

    # High-risk alerts
    high_risk_result = await session.execute(
        select(func.count(BehavioralAlertRecord.cpf_hash.distinct())).where(
            BehavioralAlertRecord.severity.in_(["high", "critical"])
        )
    )
    high_risk_players = high_risk_result.scalar() or 0

    row = ComplianceReportRow(
        report_date=target_date,
        total_players=total_players,
        active_limits=active_limits,
        new_self_exclusions=new_exclusions,
        permanent_exclusions=permanent_exclusions,
        high_risk_players=high_risk_players,
        alerts_triggered=alerts_triggered,
        welfare_blocks=0,  # populated from PAM service in production
    )

    return DailyReportResponse(
        report_date=target_date,
        generated_at=now,
        data=row,
        certification="Portaria SPA/MF 1231/2024",
    )


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@app.get("/health", summary="Health check")
async def health_check() -> Dict[str, str]:
    redis_ok = await _limit_engine._redis.ping()
    return {
        "status": "ok",
        "service": "responsible-gaming",
        "version": "1.0.0",
        "redis": "ok" if redis_ok else "degraded",
    }


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8020, reload=False)

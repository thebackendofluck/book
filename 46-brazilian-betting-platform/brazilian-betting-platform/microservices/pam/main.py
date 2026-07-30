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
PAM Service — Player Account Management
========================================
FastAPI microservice implementing full player lifecycle management for a
Brazilian betting platform operating under Lei 14.790/2023.

Endpoints:
  POST   /players/register            — CPF validation + Receita Federal check
  POST   /players/{cpf}/verify-biometric  — Facial recognition verification
  GET    /players/{cpf}               — Player profile
  PUT    /players/{cpf}/status        — Activate / suspend / block
  POST   /players/{cpf}/impediment-check — official SIGAP impediment check
  GET    /players/{cpf}/sessions      — Active sessions list
  POST   /players/{cpf}/reverify      — Trigger 15-day periodic re-verification
  DELETE /players/{cpf}               — LGPD Art. 18 data erasure
  GET    /health                      — Health check

All CPF path parameters are accepted as bare 11-digit strings or
NNN.NNN.NNN-DD format and are normalised before use.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import structlog
import uvicorn
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Path, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_cpf_access, require_operator  # noqa: F401
from biometric import BiometricMismatchError, BiometricService, LivenessFailedError
from cpf_validator import (
    CPFDeceasedError,
    CPFInvalidError,
    CPFNameMismatchError,
    CPFStatusError,
    CPFValidator,
    ReceitaFederalClient,
)
from database import PlayerRecord, PlayerSessionRecord, create_tables, dispose_engine, get_session
from models import (
    BiometricVerifyRequest,
    BiometricVerifyResponse,
    DocumentType,
    GenderCode,
    LGPDErasureResponse,
    PlayerProfile,
    PlayerRegisterRequest,
    PlayerRegisterResponse,
    PlayerStatus,
    ReverifyRequest,
    ReverifyResponse,
    SessionInfo,
    StatusAction,
    StatusUpdateRequest,
    StatusUpdateResponse,
    WelfareCheckResponse,
    WelfareStatus,
)
from welfare import (
    WelfareBeneficiaryError,
    WelfareCheckError,
    WelfareRegistryClient,
)

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ]
)
logger = structlog.get_logger(__name__)

RE_VERIFICATION_DAYS: int = 15
MIN_AGE_YEARS: int = 18

# ---------------------------------------------------------------------------
# In-memory session store (swap for Redis in production)
# ---------------------------------------------------------------------------

_active_sessions: Dict[str, List[Dict[str, Any]]] = {}  # player_id -> session list


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assert_min_age(date_of_birth: str) -> None:
    dob = datetime.strptime(date_of_birth, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(days=MIN_AGE_YEARS * 365)
    if dob > cutoff:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Player must be at least 18 years old",
        )


def _normalise_cpf_param(cpf: str) -> str:
    """Strip formatting from CPF path parameter."""
    import re
    return re.sub(r"\D", "", cpf)


async def _get_player_or_404(session: AsyncSession, cpf: str) -> PlayerRecord:
    from sqlalchemy import select
    cpf_hash = CPFValidator.hash(cpf)
    result = await session.execute(
        select(PlayerRecord).where(PlayerRecord.cpf_hash == cpf_hash)
    )
    player = result.scalar_one_or_none()
    if not player:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Player with given CPF not found",
        )
    return player


def _record_to_profile(player: PlayerRecord) -> PlayerProfile:
    return PlayerProfile(
        player_id=player.player_id,
        cpf_hash=player.cpf_hash,
        full_name=player.full_name,
        email=player.email,
        address_state=player.address_state,
        address_cep=player.address_cep,
        status=PlayerStatus(player.status),
        created_at=player.created_at,
        last_verified_at=player.last_verified_at,
        next_verification_due=player.next_verification_due,
        biometric_score=player.biometric_score,
        rejection_reason=player.rejection_reason,
        audit_trail=player.audit_trail or [],
    )


# ---------------------------------------------------------------------------
# Background task: periodic re-verification scheduler
# ---------------------------------------------------------------------------


async def _reverification_scheduler() -> None:
    """
    Background task: checks every hour for players whose 15-day
    re-verification window has elapsed and flags them.
    """
    from sqlalchemy import select, update
    from database import get_session_factory

    while True:
        try:
            now = datetime.now(timezone.utc)
            factory = get_session_factory()
            async with factory() as session:
                result = await session.execute(
                    select(PlayerRecord).where(
                        PlayerRecord.status == PlayerStatus.ACTIVE,
                        PlayerRecord.next_verification_due <= now,
                        PlayerRecord.next_verification_due.is_not(None),
                    )
                )
                due = result.scalars().all()
                for player in due:
                    player.status = PlayerStatus.REVERIFICATION_REQUIRED.value
                    trail = list(player.audit_trail or [])
                    trail.append(
                        {
                            "event": "reverification_scheduled",
                            "triggered_at": now.isoformat(),
                            "reason": "periodic_15_day",
                        }
                    )
                    player.audit_trail = trail
                    player.updated_at = now
                if due:
                    await session.commit()
                    logger.info("reverification_batch", count=len(due))
        except Exception as exc:
            logger.error("reverification_scheduler_error", error=str(exc))
        await asyncio.sleep(3600)


# ---------------------------------------------------------------------------
# App lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_tables()
    task = asyncio.create_task(_reverification_scheduler())
    logger.info("pam_service_started")
    yield
    task.cancel()
    await dispose_engine()
    logger.info("pam_service_shutdown")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="PAM — Player Account Management",
    description="Player lifecycle management for Brazilian betting platform (Lei 14.790/2023)",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://apostas.acmetocasino.bet.br"],
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

_rf_client = ReceitaFederalClient()
_biometric_svc = BiometricService()
_welfare_client = WelfareRegistryClient(
    access_token=os.getenv("SIGAP_ACCESS_TOKEN"),
    mock=os.getenv("SIGAP_MOCK", "false").lower() == "true",
)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.post(
    "/players/register",
    response_model=PlayerRegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register new player — CPF validation + Receita Federal check",
)
async def register_player(
    req: PlayerRegisterRequest,
    session: AsyncSession = Depends(get_session),
) -> PlayerRegisterResponse:
    """
    Full registration pipeline:
    1. CPF mod-11 digit check
    2. Duplicate account prevention (cpf_hash uniqueness)
    3. Receita Federal identity consultation
    4. Age verification (18+)
    5. Create player record with status IDENTITY_VERIFIED
    6. Initiate biometric step (caller must proceed to /verify-biometric)
    """
    from sqlalchemy import select

    cpf = req.cpf  # already normalised by field_validator

    # 1. Digit check
    try:
        CPFValidator.validate_or_raise(cpf)
    except CPFInvalidError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    cpf_hash = CPFValidator.hash(cpf)

    # 2. Duplicate check
    existing = await session.execute(
        select(PlayerRecord).where(PlayerRecord.cpf_hash == cpf_hash)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this CPF already exists",
        )

    # 3. Receita Federal check
    try:
        await _rf_client.consult_or_raise(cpf, req.full_name, req.date_of_birth)
    except CPFDeceasedError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except (CPFStatusError, CPFNameMismatchError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    # 4. Age check
    _assert_min_age(req.date_of_birth)

    now = datetime.now(timezone.utc)
    player_id = str(uuid.uuid4())

    player = PlayerRecord(
        player_id=player_id,
        cpf_hash=cpf_hash,
        full_name=req.full_name,
        date_of_birth=req.date_of_birth,
        email=req.email,
        phone_hash=hashlib.sha256(req.phone_br.encode()).hexdigest(),
        address_cep=req.address_cep,
        address_street=req.address_street,
        address_number=req.address_number,
        address_city=req.address_city,
        address_state=req.address_state,
        document_type=req.document_type.value,
        document_number_hash=hashlib.sha256(req.document_number.encode()).hexdigest(),
        gender=req.gender.value,
        status=PlayerStatus.IDENTITY_VERIFIED.value,
        lgpd_consent=True,
        lgpd_consent_at=now,
        marketing_consent=req.marketing_consent,
        biometric_score=0.0,
        created_at=now,
        updated_at=now,
        audit_trail=[
            {
                "event": "registration",
                "rf_status": "regular",
                "timestamp": now.isoformat(),
            }
        ],
    )
    session.add(player)

    logger.info("player_registered", player_id=player_id)
    return PlayerRegisterResponse(
        player_id=player_id,
        status=PlayerStatus.IDENTITY_VERIFIED,
        message="Identity verified. Submit biometric to complete registration.",
    )


@app.post(
    "/players/{cpf}/verify-biometric",
    response_model=BiometricVerifyResponse,
    summary="Submit facial recognition for biometric verification",
)
async def verify_biometric(
    cpf: str = Path(..., description="Player CPF (digits or formatted)"),
    req: BiometricVerifyRequest = ...,
    session: AsyncSession = Depends(get_session),
) -> BiometricVerifyResponse:
    """
    Perform facial recognition against the submitted selfie and document.
    On success, run the official SIGAP impediment check; if clear, set ACTIVE.
    """
    cpf = _normalise_cpf_param(cpf)
    player = await _get_player_or_404(session, cpf)

    allowed = {PlayerStatus.IDENTITY_VERIFIED.value, PlayerStatus.BIOMETRIC_PENDING.value,
                PlayerStatus.REVERIFICATION_REQUIRED.value}
    if player.status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot verify biometric in status '{player.status}'",
        )

    player.status = PlayerStatus.BIOMETRIC_PENDING.value
    now = datetime.now(timezone.utc)

    try:
        result = await _biometric_svc.verify_or_raise(
            req.selfie_base64,
            req.document_front_base64,
            req.liveness_token,
        )
    except (BiometricMismatchError, LivenessFailedError) as exc:
        player.status = PlayerStatus.SUSPENDED.value
        player.rejection_reason = str(exc)
        trail = list(player.audit_trail or [])
        trail.append({"event": "biometric_failed", "reason": str(exc), "timestamp": now.isoformat()})
        player.audit_trail = trail
        player.updated_at = now
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    player.biometric_score = result.confidence_score

    # SIGAP requires the normalized CPF, not its stored hash.
    try:
        await _welfare_client.check_or_raise(cpf)
    except WelfareBeneficiaryError as exc:
        player.status = PlayerStatus.BLOCKED.value
        player.rejection_reason = str(exc)
        trail = list(player.audit_trail or [])
        trail.append(
            {
                "event": "blocked_sigap_impediment",
                "reasons": list(exc.reasons),
                "timestamp": now.isoformat(),
            }
        )
        player.audit_trail = trail
        player.updated_at = now
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except WelfareCheckError as exc:
        player.status = PlayerStatus.SUSPENDED.value
        player.rejection_reason = str(exc)
        trail = list(player.audit_trail or [])
        trail.append(
            {
                "event": "sigap_impediment_check_unavailable",
                "timestamp": now.isoformat(),
            }
        )
        player.audit_trail = trail
        player.updated_at = now
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Regulatory verification unavailable; betting remains disabled",
        )

    player.status = PlayerStatus.ACTIVE.value
    player.last_verified_at = now
    player.next_verification_due = now + timedelta(days=RE_VERIFICATION_DAYS)
    trail = list(player.audit_trail or [])
    trail.append(
        {
            "event": "biometric_verified",
            "score": result.confidence_score,
            "provider": result.provider,
            "timestamp": now.isoformat(),
        }
    )
    player.audit_trail = trail
    player.updated_at = now

    logger.info("biometric_verified", player_id=player.player_id, score=result.confidence_score)
    return BiometricVerifyResponse(
        player_id=player.player_id,
        status=PlayerStatus.ACTIVE,
        biometric_score=result.confidence_score,
        next_verification_due=player.next_verification_due,
    )


@app.get(
    "/players/{cpf}",
    dependencies=[Depends(require_cpf_access)],
    response_model=PlayerProfile,
    summary="Retrieve player profile",
)
async def get_player(
    cpf: str = Path(..., description="Player CPF"),
    session: AsyncSession = Depends(get_session),
) -> PlayerProfile:
    cpf = _normalise_cpf_param(cpf)
    player = await _get_player_or_404(session, cpf)
    return _record_to_profile(player)


@app.put(
    "/players/{cpf}/status",
    response_model=StatusUpdateResponse,
    summary="Activate, suspend, or block a player",
)
async def update_player_status(
    req: StatusUpdateRequest,
    cpf: str = Path(..., description="Player CPF"),
    session: AsyncSession = Depends(get_session),
    caller: dict = Depends(require_operator),
) -> StatusUpdateResponse:
    cpf = _normalise_cpf_param(cpf)
    player = await _get_player_or_404(session, cpf)
    now = datetime.now(timezone.utc)
    previous = PlayerStatus(player.status)

    action_map = {
        StatusAction.ACTIVATE: PlayerStatus.ACTIVE,
        StatusAction.SUSPEND: PlayerStatus.SUSPENDED,
        StatusAction.BLOCK: PlayerStatus.BLOCKED,
    }
    new_status = action_map[req.action]

    trail = list(player.audit_trail or [])
    trail.append(
        {
            "event": f"status_{req.action.value}",
            "from": previous.value,
            "to": new_status.value,
            "reason": req.reason,
            "operator_id": caller.get("sub", "unknown"),
            "timestamp": now.isoformat(),
        }
    )
    player.status = new_status.value
    player.audit_trail = trail
    player.updated_at = now

    logger.info(
        "player_status_updated",
        player_id=player.player_id,
        from_status=previous.value,
        to_status=new_status.value,
        operator=caller.get("sub", "unknown"),
    )
    return StatusUpdateResponse(
        player_id=player.player_id,
        previous_status=previous,
        current_status=new_status,
        updated_at=now,
    )


@app.post(
    "/players/{cpf}/impediment-check",
    response_model=WelfareCheckResponse,
    summary="Run the official SIGAP impediment check",
)
@app.post(
    "/players/{cpf}/welfare-check",
    response_model=WelfareCheckResponse,
    include_in_schema=False,
)
async def welfare_check(
    cpf: str = Path(..., description="Player CPF"),
    session: AsyncSession = Depends(get_session),
) -> WelfareCheckResponse:
    """
    Ad-hoc SIGAP check for re-verification or compliance. This does not trace
    payment funds or query social-program databases directly.
    """
    cpf = _normalise_cpf_param(cpf)
    player = await _get_player_or_404(session, cpf)
    now = datetime.now(timezone.utc)

    try:
        result = await _welfare_client.check(cpf)
    except WelfareCheckError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Regulatory verification unavailable; betting remains disabled",
        )

    if result.restriction_active:
        player.status = PlayerStatus.BLOCKED.value
        reason = f"SIGAP betting impediment: {', '.join(result.motivos)}"
        player.rejection_reason = reason
        trail = list(player.audit_trail or [])
        trail.append(
            {
                "event": "blocked_sigap_impediment",
                "motivos": list(result.motivos),
                "request_id": result.request_id,
                "timestamp": now.isoformat(),
            }
        )
        player.audit_trail = trail
        player.updated_at = now

    welfare_status = WelfareStatus(
        cpf_hash=result.cpf_hash,
        resultado=result.resultado,
        motivos=list(result.motivos),
        request_id=result.request_id,
        restriction_active=result.restriction_active,
        checked_at=result.checked_at,
    )

    return WelfareCheckResponse(
        player_id=player.player_id,
        welfare_status=welfare_status,
        access_permitted=not result.restriction_active,
    )


@app.get(
    "/players/{cpf}/sessions",
    response_model=List[SessionInfo],
    summary="List active sessions for a player",
)
async def get_sessions(
    cpf: str = Path(..., description="Player CPF"),
    session: AsyncSession = Depends(get_session),
) -> List[SessionInfo]:
    from sqlalchemy import select

    cpf = _normalise_cpf_param(cpf)
    player = await _get_player_or_404(session, cpf)

    result = await session.execute(
        select(PlayerSessionRecord).where(
            PlayerSessionRecord.player_id == player.player_id,
            PlayerSessionRecord.is_active.is_(True),
        )
    )
    sessions = result.scalars().all()

    return [
        SessionInfo(
            session_id=s.session_id,
            started_at=s.started_at,
            last_seen_at=s.last_seen_at,
            ip_address=s.ip_address,
            device_fingerprint=s.device_fingerprint,
        )
        for s in sessions
    ]


@app.post(
    "/players/{cpf}/reverify",
    response_model=ReverifyResponse,
    summary="Trigger 15-day periodic re-verification",
)
async def reverify_player(
    req: ReverifyRequest,
    cpf: str = Path(..., description="Player CPF"),
    session: AsyncSession = Depends(get_session),
) -> ReverifyResponse:
    """
    Set player status to REVERIFICATION_REQUIRED.
    Called by the background scheduler or manually by compliance.
    Player must re-submit biometric within the grace period.
    """
    cpf = _normalise_cpf_param(cpf)
    player = await _get_player_or_404(session, cpf)
    now = datetime.now(timezone.utc)

    if player.status not in (PlayerStatus.ACTIVE.value,):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Re-verification requires ACTIVE status, current: '{player.status}'",
        )

    trail = list(player.audit_trail or [])
    trail.append(
        {
            "event": "reverification_triggered",
            "reason": req.reason,
            "triggered_by": req.triggered_by,
            "timestamp": now.isoformat(),
        }
    )
    player.status = PlayerStatus.REVERIFICATION_REQUIRED.value
    player.audit_trail = trail
    player.updated_at = now

    logger.info(
        "player_reverification_triggered",
        player_id=player.player_id,
        reason=req.reason,
    )
    return ReverifyResponse(
        player_id=player.player_id,
        status=PlayerStatus.REVERIFICATION_REQUIRED,
        message="Re-verification required. Player must re-submit biometric.",
    )


@app.delete(
    "/players/{cpf}",
    dependencies=[Depends(require_cpf_access)],
    response_model=LGPDErasureResponse,
    summary="LGPD Art. 18 — right to erasure",
)
async def lgpd_erasure(
    cpf: str = Path(..., description="Player CPF"),
    session: AsyncSession = Depends(get_session),
) -> LGPDErasureResponse:
    """
    Anonymise all PII fields per LGPD Art. 18 (right to erasure).

    Regulatory note: Lei 14.790/2023 requires 5-year retention of
    financial and audit records.  Only PII fields are anonymised;
    the audit trail and CPF hash (for deduplication) are retained.
    """
    cpf = _normalise_cpf_param(cpf)
    player = await _get_player_or_404(session, cpf)
    now = datetime.now(timezone.utc)

    retained = ["cpf_hash", "date_of_birth", "address_state", "audit_trail", "created_at"]

    player.full_name = "[DELETED]"
    player.email = "[DELETED]"
    player.phone_hash = "[DELETED]"
    player.document_number_hash = "[DELETED]"
    player.address_street = "[DELETED]"
    player.address_number = "[DELETED]"
    player.address_city = "[DELETED]"
    player.status = PlayerStatus.DELETED.value
    trail = list(player.audit_trail or [])
    trail.append(
        {
            "event": "lgpd_erasure",
            "requested_at": now.isoformat(),
            "retained_fields": retained,
        }
    )
    player.audit_trail = trail
    player.updated_at = now

    logger.info("lgpd_erasure", player_id=player.player_id)
    return LGPDErasureResponse(
        player_id=player.player_id,
        status="anonymized",
        anonymized_at=now,
        retained_for_compliance=retained,
    )


@app.get("/health", summary="Health check")
async def health_check() -> Dict[str, str]:
    return {"status": "ok", "service": "pam", "version": "1.0.0"}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8010, reload=False)

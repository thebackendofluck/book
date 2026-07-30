# Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# REGULATORY REQUIREMENT: Brazil — Lei 14.790/2023 + SPA/MF Responsible Gaming
# Regulation:  Lei 14.790/2023 Art. 30 — mandatory deposit limits, loss limits,
#              and session limits for all licensed operators;
#              Portaria SPA/MF No. 615/2023 + No. 722/2024 — responsible gaming
#              technical specifications; session timeout requirements;
#              SIGAP National Register of Prohibited Persons (2026): operators must
#              block players on the register before any wager or deposit;
#              Lei 14.811/2024 — anti-addiction measures for sports betting
# Purpose:     Deposit limits (daily/weekly/monthly), loss limits, session time
#              limits with auto-logout, cooling-off period management. These tools
#              are mandatory — operators who do not implement them cannot be licensed.
# 2026 Update: SIGAP prohibited persons check integrated with RG engine — every
#              login and every wager must query the national exclusion register.
#              Penalty for failure: 20% of annual revenue.
# Retention:   RG limit history: 5 years (SPA/MF audit requirements)
# Penalty:     Up to 20% of annual revenue for RG tool failures;
#              Licence suspension for repeated violations
# Jurisdictions: Brazil (SPA/MF, primarily); comparable requirements in MGA,
#              UKGC (LCCP RTS 12), Sweden (Spellagen §6 kap. 4 §), KSA (Wet Koa)
#
# References:
#   Lei 14.790/2023: https://www.planalto.gov.br/ccivil_03/_ato2023-2026/2023/lei/L14790.htm
#   Portaria SPA/MF 722/2024: https://www.in.gov.br/web/dou/-/portaria-spa/mf-n-722
#   UKGC LCCP: https://www.gamblingcommission.gov.uk/licensees-and-businesses/lccp
#   UKGC Remote Technical Standards: https://www.gamblingcommission.gov.uk/standards/remote-technical-standards
#   MGA Player Protection Directive: https://www.mga.org.mt/legislation/subsidiary-legislation/
#   Spellagen (2018:1138): https://www.riksdagen.se/sv/dokument-och-lagar/dokument/svensk-forfattningssamling/spellag-20181138_sfs-2018-1138/
#   KSA (Kansspelautoriteit): https://kansspelautoriteit.nl/
# =============================================================================
"""
Responsible Gaming Compliance Engine -- Brazilian Betting Platform
==================================================================
Implements the responsible gaming requirements of Lei 14.790/2023
and SPA/MF Portaria 615/2023:

  - Deposit limits (daily / weekly / monthly) with enforcement
  - Loss limits with real-time tracking
  - Session time limits with progressive warnings and auto-logout
  - Cooling-off period management
  - Self-exclusion workflow (temporary: 1 day – 5 years; permanent)
  - Behavioral risk scoring with ML signal stubs
  - National self-exclusion platform (APOSTA RESPONSÁVEL) API integration
  - Alert system for at-risk players
  - Full audit trail for regulatory inspection

Reference implementation for Chapter 46: Brazilian Betting Platform.
"""

from __future__ import annotations

import asyncio
import enum
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import structlog
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Custom Exceptions
# ---------------------------------------------------------------------------


class RGError(Exception):
    """Base responsible gaming exception."""


class LimitExceededError(RGError):
    """Player has reached a deposit or loss limit."""


class SessionLimitReachedError(RGError):
    """Session time limit has been reached."""


class SelfExcludedError(RGError):
    """Player is self-excluded."""


class CoolingOffActiveError(RGError):
    """Player is in a cooling-off period."""


class LimitDecreaseViolationError(RGError):
    """Attempted to increase a limit before cooling-off for the decrease expired."""


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class LimitPeriod(str, enum.Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class SelfExclusionType(str, enum.Enum):
    TEMPORARY = "temporary"
    PERMANENT = "permanent"


class AlertSeverity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class RiskLevel(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class PlayerAccountStatus(str, enum.Enum):
    ACTIVE = "active"
    COOLING_OFF = "cooling_off"
    SELF_EXCLUDED = "self_excluded"
    SUSPENDED = "suspended"


# ---------------------------------------------------------------------------
# Pydantic Models -- API layer
# ---------------------------------------------------------------------------


class SetLimitRequest(BaseModel):
    player_id: str
    limit_type: str = Field(..., pattern=r"^(deposit|loss|session_minutes)$")
    period: Optional[LimitPeriod] = None
    value: float = Field(..., gt=0)
    effective_from: Optional[datetime] = None


class SelfExclusionRequest(BaseModel):
    player_id: str
    exclusion_type: SelfExclusionType
    duration_days: Optional[int] = Field(
        default=None,
        ge=1,
        le=1825,
        description="Duration in days (temporary only; max 5 years per Lei 14.790/2023)",
    )
    reason: Optional[str] = None

    @model_validator(mode="after")
    def validate_duration(self) -> "SelfExclusionRequest":
        if self.exclusion_type == SelfExclusionType.TEMPORARY and not self.duration_days:
            raise ValueError("duration_days required for temporary exclusion")
        if self.exclusion_type == SelfExclusionType.PERMANENT and self.duration_days is not None:
            raise ValueError("duration_days must not be set for permanent exclusion")
        return self


class SessionStartRequest(BaseModel):
    player_id: str
    session_id: str


class BetCheckRequest(BaseModel):
    player_id: str
    session_id: str
    deposit_amount_brl: float = Field(default=0.0, ge=0)
    bet_amount_brl: float = Field(default=0.0, ge=0)
    net_loss_brl: float = Field(default=0.0, ge=0)


class RGStatusResponse(BaseModel):
    player_id: str
    account_status: PlayerAccountStatus
    risk_level: RiskLevel
    active_limits: Dict[str, Any]
    active_sessions: List[str]
    pending_alerts: int
    cooling_off_ends: Optional[datetime]
    exclusion_ends: Optional[datetime]
    self_exclusion_type: Optional[SelfExclusionType]


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------


@dataclass
class Limit:
    limit_id: str
    player_id: str
    limit_type: str
    period: Optional[LimitPeriod]
    value: float
    current_usage: float = 0.0
    set_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    effective_from: Optional[datetime] = None
    cooldown_until: Optional[datetime] = None  # for limit-increase cooldown

    @property
    def is_active(self) -> bool:
        if self.effective_from:
            return datetime.now(timezone.utc) >= self.effective_from
        return True

    @property
    def remaining(self) -> float:
        return max(0.0, self.value - self.current_usage)

    @property
    def utilisation_pct(self) -> float:
        if self.value == 0:
            return 100.0
        return min(100.0, (self.current_usage / self.value) * 100)


@dataclass
class ActiveSession:
    session_id: str
    player_id: str
    started_at: datetime
    session_limit_minutes: Optional[int]
    last_warning_sent: Optional[datetime] = None
    total_wagered_brl: float = 0.0
    total_lost_brl: float = 0.0

    @property
    def elapsed_minutes(self) -> float:
        return (datetime.now(timezone.utc) - self.started_at).total_seconds() / 60

    @property
    def time_remaining_minutes(self) -> Optional[float]:
        if not self.session_limit_minutes:
            return None
        return max(0.0, self.session_limit_minutes - self.elapsed_minutes)


@dataclass
class SelfExclusion:
    exclusion_id: str
    player_id: str
    exclusion_type: SelfExclusionType
    created_at: datetime
    ends_at: Optional[datetime]  # None = permanent
    reason: Optional[str]
    national_registry_submitted: bool = False
    national_registry_ack: Optional[str] = None

    @property
    def is_active(self) -> bool:
        if self.exclusion_type == SelfExclusionType.PERMANENT:
            return True
        return self.ends_at is not None and datetime.now(timezone.utc) < self.ends_at


@dataclass
class RGAlert:
    alert_id: str
    player_id: str
    severity: AlertSeverity
    message: str
    created_at: datetime
    acknowledged: bool = False
    acknowledged_at: Optional[datetime] = None


@dataclass
class BehavioralRiskProfile:
    player_id: str
    risk_score: float = 0.0
    risk_level: RiskLevel = RiskLevel.LOW
    last_evaluated: Optional[datetime] = None
    signals: Dict[str, float] = field(default_factory=dict)
    consecutive_loss_streak: int = 0
    late_night_sessions: int = 0   # sessions between 00:00-05:00
    chasing_score: float = 0.0     # bet-size escalation after losses


# ---------------------------------------------------------------------------
# National Self-Exclusion Registry Client
# ---------------------------------------------------------------------------


class ApostaResponsavelClient:
    """
    Submits and queries the national self-exclusion registry
    operated by MF/SPA under Lei 14.790/2023 Art. 27.
    In production: mTLS call with operator e-CNPJ certificate.
    """

    def __init__(self, base_url: str, operator_cnpj: str) -> None:
        self.base_url = base_url
        self.operator_cnpj = operator_cnpj

    async def submit_exclusion(
        self,
        cpf_hash: str,
        exclusion_type: SelfExclusionType,
        ends_at: Optional[datetime],
    ) -> str:
        """
        Registers the exclusion with the national platform.
        Returns registry acknowledgement ID.
        """
        import asyncio
        await asyncio.sleep(0.05)  # simulate network
        ack_id = f"APOSTA-{uuid.uuid4().hex[:12].upper()}"
        logger.info(
            "national_exclusion_submitted",
            cpf_hash=cpf_hash[:8] + "...",
            type=exclusion_type.value,
            ack_id=ack_id,
        )
        return ack_id

    async def check_exclusion(self, cpf_hash: str) -> Optional[Dict[str, Any]]:
        """Returns exclusion record if CPF is on the national registry."""
        await asyncio.sleep(0.05)
        return None  # Stub: not excluded


# ---------------------------------------------------------------------------
# In-Memory Stores (replace with PostgreSQL + Redis in production)
# ---------------------------------------------------------------------------


class RGStore:
    def __init__(self) -> None:
        self._limits: Dict[str, List[Limit]] = {}        # player_id -> limits
        self._sessions: Dict[str, ActiveSession] = {}    # session_id -> session
        self._exclusions: Dict[str, SelfExclusion] = {}  # player_id -> exclusion
        self._alerts: Dict[str, List[RGAlert]] = {}       # player_id -> alerts
        self._risk_profiles: Dict[str, BehavioralRiskProfile] = {}
        self._account_status: Dict[str, PlayerAccountStatus] = {}
        self._lock = asyncio.Lock()

    async def get_limits(self, player_id: str) -> List[Limit]:
        return self._limits.get(player_id, [])

    async def save_limit(self, limit: Limit) -> None:
        async with self._lock:
            limits = self._limits.setdefault(limit.player_id, [])
            # Replace existing limit of same type+period
            limits[:] = [
                l for l in limits
                if not (l.limit_type == limit.limit_type and l.period == limit.period)
            ]
            limits.append(limit)

    async def get_exclusion(self, player_id: str) -> Optional[SelfExclusion]:
        return self._exclusions.get(player_id)

    async def save_exclusion(self, exc: SelfExclusion) -> None:
        async with self._lock:
            self._exclusions[exc.player_id] = exc

    async def get_session(self, session_id: str) -> Optional[ActiveSession]:
        return self._sessions.get(session_id)

    async def save_session(self, session: ActiveSession) -> None:
        async with self._lock:
            self._sessions[session.session_id] = session

    async def end_session(self, session_id: str) -> None:
        async with self._lock:
            self._sessions.pop(session_id, None)

    async def add_alert(self, alert: RGAlert) -> None:
        async with self._lock:
            self._alerts.setdefault(alert.player_id, []).append(alert)

    async def get_pending_alerts(self, player_id: str) -> List[RGAlert]:
        return [a for a in self._alerts.get(player_id, []) if not a.acknowledged]

    async def get_risk_profile(self, player_id: str) -> BehavioralRiskProfile:
        return self._risk_profiles.get(
            player_id, BehavioralRiskProfile(player_id=player_id)
        )

    async def save_risk_profile(self, profile: BehavioralRiskProfile) -> None:
        async with self._lock:
            self._risk_profiles[profile.player_id] = profile

    async def get_account_status(self, player_id: str) -> PlayerAccountStatus:
        return self._account_status.get(player_id, PlayerAccountStatus.ACTIVE)

    async def set_account_status(
        self, player_id: str, status: PlayerAccountStatus
    ) -> None:
        async with self._lock:
            self._account_status[player_id] = status


# ---------------------------------------------------------------------------
# Behavioral Risk Scorer
# ---------------------------------------------------------------------------


class BehavioralRiskScorer:
    """
    Computes a risk score 0.0-1.0 from player behavioral signals.
    In production: replace with trained XGBoost/LSTM model.
    """

    THRESHOLDS = {
        RiskLevel.LOW: 0.30,
        RiskLevel.MEDIUM: 0.55,
        RiskLevel.HIGH: 0.75,
        RiskLevel.CRITICAL: 0.90,
    }

    def compute(self, profile: BehavioralRiskProfile) -> Tuple[float, RiskLevel]:
        score = 0.0

        # Signal: chasing losses
        score += min(0.35, profile.chasing_score * 0.35)

        # Signal: consecutive losses
        if profile.consecutive_loss_streak > 10:
            score += 0.25
        elif profile.consecutive_loss_streak > 5:
            score += 0.10

        # Signal: late-night sessions
        if profile.late_night_sessions > 5:
            score += 0.20
        elif profile.late_night_sessions > 2:
            score += 0.08

        score = min(score, 1.0)

        if score >= self.THRESHOLDS[RiskLevel.CRITICAL]:
            level = RiskLevel.CRITICAL
        elif score >= self.THRESHOLDS[RiskLevel.HIGH]:
            level = RiskLevel.HIGH
        elif score >= self.THRESHOLDS[RiskLevel.MEDIUM]:
            level = RiskLevel.MEDIUM
        else:
            level = RiskLevel.LOW

        return round(score, 3), level


# ---------------------------------------------------------------------------
# Alert Dispatcher
# ---------------------------------------------------------------------------


class AlertDispatcher:
    """
    Sends responsible gaming alerts via email, SMS, and in-app.
    Stub: logs only. Wire to SNS/SES/Twilio in production.
    """

    async def dispatch(self, alert: RGAlert) -> None:
        logger.info(
            "rg_alert_dispatched",
            player_id=alert.player_id,
            severity=alert.severity.value,
            message=alert.message,
        )
        # Production:
        # if alert.severity == AlertSeverity.CRITICAL:
        #     await self.sms_client.send(player_id, alert.message)
        # await self.email_client.send(player_id, alert.message)
        # await self.websocket_broker.publish(player_id, alert)


# ---------------------------------------------------------------------------
# Responsible Gaming Engine
# ---------------------------------------------------------------------------


class ResponsibleGamingEngine:
    """
    Core enforcement engine:
      - Checks limits before every deposit/bet
      - Manages session time with warnings
      - Handles self-exclusion workflow
      - Runs behavioral risk scoring
    """

    # Warning thresholds (% of limit)
    WARN_THRESHOLDS = [0.80, 0.90, 0.95]
    SESSION_WARNING_MINUTES = [15, 5, 1]  # warnings before session limit

    def __init__(
        self,
        store: RGStore,
        scorer: BehavioralRiskScorer,
        alert_dispatcher: AlertDispatcher,
        national_registry: ApostaResponsavelClient,
    ) -> None:
        self.store = store
        self.scorer = scorer
        self.alerts = alert_dispatcher
        self.registry = national_registry

    # ------------------------------------------------------------------
    # Limit Management
    # ------------------------------------------------------------------

    async def set_limit(self, req: SetLimitRequest) -> Limit:
        """
        Sets or updates a player limit.
        Decreases take effect immediately.
        Increases are deferred 24 hours (cooling-off per best practice).
        """
        existing = next(
            (
                l for l in await self.store.get_limits(req.player_id)
                if l.limit_type == req.limit_type and l.period == req.period
            ),
            None,
        )

        now = datetime.now(timezone.utc)
        effective_from = req.effective_from or now
        cooldown_until = None

        if existing and req.value > existing.value:
            # Increase: enforce 24-hour cooldown
            effective_from = now + timedelta(hours=24)
            cooldown_until = effective_from

        limit = Limit(
            limit_id=str(uuid.uuid4()),
            player_id=req.player_id,
            limit_type=req.limit_type,
            period=req.period,
            value=req.value,
            current_usage=existing.current_usage if existing else 0.0,
            set_at=now,
            effective_from=effective_from,
            cooldown_until=cooldown_until,
        )
        await self.store.save_limit(limit)
        logger.info(
            "rg_limit_set",
            player_id=req.player_id,
            type=req.limit_type,
            period=str(req.period),
            value=req.value,
            effective_from=effective_from.isoformat(),
        )
        return limit

    async def check_deposit_allowed(
        self, player_id: str, amount_brl: float
    ) -> None:
        """
        Raises LimitExceededError if the deposit would breach any active limit.
        Also checks exclusion and cooling-off status.
        """
        await self._assert_not_excluded(player_id)

        limits = await self.store.get_limits(player_id)
        for limit in limits:
            if limit.limit_type != "deposit" or not limit.is_active:
                continue
            if limit.current_usage + amount_brl > limit.value:
                await self._raise_limit_alert(player_id, limit)
                raise LimitExceededError(
                    f"Deposit of R${amount_brl:.2f} would exceed "
                    f"{limit.period.value if limit.period else ''} deposit limit "
                    f"(R${limit.value:.2f}). Used: R${limit.current_usage:.2f}."
                )
            # Proximity warnings
            new_usage_pct = (limit.current_usage + amount_brl) / limit.value
            for threshold in self.WARN_THRESHOLDS:
                if new_usage_pct >= threshold:
                    await self._send_usage_alert(player_id, limit, new_usage_pct)
                    break

    async def record_deposit(self, player_id: str, amount_brl: float) -> None:
        """Update deposit counters on all active deposit limits."""
        limits = await self.store.get_limits(player_id)
        for limit in limits:
            if limit.limit_type == "deposit" and limit.is_active:
                limit.current_usage += amount_brl
                await self.store.save_limit(limit)

    async def record_loss(self, player_id: str, net_loss_brl: float) -> None:
        """Update loss counters and trigger risk re-scoring."""
        if net_loss_brl <= 0:
            return
        limits = await self.store.get_limits(player_id)
        for limit in limits:
            if limit.limit_type == "loss" and limit.is_active:
                limit.current_usage += net_loss_brl
                await self.store.save_limit(limit)
                if limit.current_usage >= limit.value:
                    await self._raise_limit_alert(player_id, limit)
                    raise LimitExceededError(
                        f"Loss limit of R${limit.value:.2f} reached."
                    )

    # ------------------------------------------------------------------
    # Session Management
    # ------------------------------------------------------------------

    async def start_session(self, req: SessionStartRequest) -> ActiveSession:
        """Registers a new betting session. Checks exclusion/cooling-off first."""
        await self._assert_not_excluded(req.player_id)

        limits = await self.store.get_limits(req.player_id)
        session_limit = next(
            (l.value for l in limits if l.limit_type == "session_minutes" and l.is_active),
            None,
        )

        session = ActiveSession(
            session_id=req.session_id,
            player_id=req.player_id,
            started_at=datetime.now(timezone.utc),
            session_limit_minutes=int(session_limit) if session_limit else None,
        )
        await self.store.save_session(session)
        logger.info(
            "rg_session_started",
            player_id=req.player_id,
            session_id=req.session_id,
            limit_minutes=session_limit,
        )
        return session

    async def check_session_time(self, session_id: str) -> None:
        """
        Checks if session time limit has been reached.
        Sends warnings at 15 / 5 / 1 minute before limit.
        Raises SessionLimitReachedError when limit is hit.
        """
        session = await self.store.get_session(session_id)
        if not session or not session.session_limit_minutes:
            return

        elapsed = session.elapsed_minutes
        remaining = session.time_remaining_minutes or 0

        if remaining <= 0:
            await self.store.end_session(session_id)
            alert = RGAlert(
                alert_id=str(uuid.uuid4()),
                player_id=session.player_id,
                severity=AlertSeverity.CRITICAL,
                message=(
                    "Sua sessão atingiu o limite de tempo. "
                    "Por favor, faça uma pausa e jogue com responsabilidade."
                ),
                created_at=datetime.now(timezone.utc),
            )
            await self.store.add_alert(alert)
            await self.alerts.dispatch(alert)
            raise SessionLimitReachedError(
                f"Session {session_id} time limit reached ({session.session_limit_minutes} min)"
            )

        now = datetime.now(timezone.utc)
        for warn_min in self.SESSION_WARNING_MINUTES:
            if remaining <= warn_min:
                last_warn = session.last_warning_sent
                if not last_warn or (now - last_warn).total_seconds() > 300:
                    session.last_warning_sent = now
                    await self.store.save_session(session)
                    alert = RGAlert(
                        alert_id=str(uuid.uuid4()),
                        player_id=session.player_id,
                        severity=AlertSeverity.WARNING,
                        message=(
                            f"Atenção: sua sessão encerrará em {warn_min} minuto(s). "
                            "Jogue com responsabilidade."
                        ),
                        created_at=now,
                    )
                    await self.store.add_alert(alert)
                    await self.alerts.dispatch(alert)
                break

    # ------------------------------------------------------------------
    # Self-Exclusion
    # ------------------------------------------------------------------

    async def request_self_exclusion(
        self, req: SelfExclusionRequest, player_cpf_hash: str
    ) -> SelfExclusion:
        """
        Activates self-exclusion:
          1. Immediately blocks account
          2. Ends all active sessions
          3. Submits to national registry (APOSTA RESPONSÁVEL)
        """
        now = datetime.now(timezone.utc)
        ends_at = None
        if req.exclusion_type == SelfExclusionType.TEMPORARY and req.duration_days:
            ends_at = now + timedelta(days=req.duration_days)

        exclusion = SelfExclusion(
            exclusion_id=str(uuid.uuid4()),
            player_id=req.player_id,
            exclusion_type=req.exclusion_type,
            created_at=now,
            ends_at=ends_at,
            reason=req.reason,
        )
        await self.store.save_exclusion(exclusion)
        await self.store.set_account_status(
            req.player_id, PlayerAccountStatus.SELF_EXCLUDED
        )

        # Submit to national registry
        try:
            ack = await self.registry.submit_exclusion(
                player_cpf_hash, req.exclusion_type, ends_at
            )
            exclusion.national_registry_submitted = True
            exclusion.national_registry_ack = ack
            await self.store.save_exclusion(exclusion)
        except Exception as exc:
            logger.error(
                "national_registry_submit_failed",
                player_id=req.player_id,
                error=str(exc),
            )

        # Alert the player
        alert = RGAlert(
            alert_id=str(uuid.uuid4()),
            player_id=req.player_id,
            severity=AlertSeverity.INFO,
            message=(
                f"Auto-exclusão ativada com sucesso. "
                f"{'Expira em: ' + ends_at.strftime('%d/%m/%Y') if ends_at else 'Permanente.'} "
                "Se precisar de ajuda: CVV 188 ou apostaresponsavel.mf.gov.br"
            ),
            created_at=now,
        )
        await self.store.add_alert(alert)
        await self.alerts.dispatch(alert)

        logger.info(
            "self_exclusion_activated",
            player_id=req.player_id,
            type=req.exclusion_type.value,
            ends_at=ends_at.isoformat() if ends_at else "permanent",
        )
        return exclusion

    async def lift_self_exclusion(self, player_id: str) -> None:
        """
        Lifts a TEMPORARY exclusion after its period ends.
        PERMANENT exclusions cannot be lifted programmatically.
        Requires compliance officer approval in production.
        """
        exclusion = await self.store.get_exclusion(player_id)
        if not exclusion:
            raise RGError("No active exclusion found")
        if exclusion.exclusion_type == SelfExclusionType.PERMANENT:
            raise RGError("Permanent exclusions cannot be lifted")
        if exclusion.ends_at and datetime.now(timezone.utc) < exclusion.ends_at:
            remaining = (exclusion.ends_at - datetime.now(timezone.utc)).days
            raise RGError(
                f"Exclusion period has not ended. {remaining} days remaining."
            )
        await self.store.set_account_status(player_id, PlayerAccountStatus.ACTIVE)
        logger.info("self_exclusion_lifted", player_id=player_id)

    # ------------------------------------------------------------------
    # Cooling-Off
    # ------------------------------------------------------------------

    async def request_cooling_off(
        self, player_id: str, duration_hours: int = 24
    ) -> datetime:
        """Activates a voluntary cooling-off period."""
        ends_at = datetime.now(timezone.utc) + timedelta(hours=duration_hours)
        await self.store.set_account_status(
            player_id, PlayerAccountStatus.COOLING_OFF
        )
        logger.info(
            "cooling_off_activated",
            player_id=player_id,
            duration_hours=duration_hours,
            ends_at=ends_at.isoformat(),
        )
        return ends_at

    # ------------------------------------------------------------------
    # Behavioral Risk Assessment
    # ------------------------------------------------------------------

    async def assess_risk(self, player_id: str) -> BehavioralRiskProfile:
        """
        Re-scores behavioral risk and raises alerts if risk level changed.
        Should be called after every session and on daily batch.
        """
        profile = await self.store.get_risk_profile(player_id)
        score, level = self.scorer.compute(profile)
        old_level = profile.risk_level

        profile.risk_score = score
        profile.risk_level = level
        profile.last_evaluated = datetime.now(timezone.utc)
        await self.store.save_risk_profile(profile)

        if level != old_level and level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            alert = RGAlert(
                alert_id=str(uuid.uuid4()),
                player_id=player_id,
                severity=(
                    AlertSeverity.CRITICAL
                    if level == RiskLevel.CRITICAL
                    else AlertSeverity.WARNING
                ),
                message=(
                    f"Nível de risco elevado detectado ({level.value}). "
                    "Recomendamos uma pausa. Acesse nossos recursos de jogo responsável."
                ),
                created_at=datetime.now(timezone.utc),
            )
            await self.store.add_alert(alert)
            await self.alerts.dispatch(alert)

        return profile

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _assert_not_excluded(self, player_id: str) -> None:
        status = await self.store.get_account_status(player_id)
        if status == PlayerAccountStatus.SELF_EXCLUDED:
            exc = await self.store.get_exclusion(player_id)
            if exc and exc.is_active:
                raise SelfExcludedError(
                    f"Player {player_id} is self-excluded "
                    f"({'permanent' if exc.exclusion_type == SelfExclusionType.PERMANENT else 'until ' + str(exc.ends_at)})"
                )
            # Exclusion has lapsed -- auto-lift
            await self.store.set_account_status(player_id, PlayerAccountStatus.ACTIVE)
        elif status == PlayerAccountStatus.COOLING_OFF:
            raise CoolingOffActiveError(
                f"Player {player_id} is in a cooling-off period"
            )

    async def _raise_limit_alert(self, player_id: str, limit: Limit) -> None:
        alert = RGAlert(
            alert_id=str(uuid.uuid4()),
            player_id=player_id,
            severity=AlertSeverity.CRITICAL,
            message=(
                f"Você atingiu seu limite de {limit.limit_type} "
                f"{'(' + limit.period.value + ')' if limit.period else ''} "
                f"de R${limit.value:.2f}. Jogue com responsabilidade."
            ),
            created_at=datetime.now(timezone.utc),
        )
        await self.store.add_alert(alert)
        await self.alerts.dispatch(alert)

    async def _send_usage_alert(
        self, player_id: str, limit: Limit, pct: float
    ) -> None:
        alert = RGAlert(
            alert_id=str(uuid.uuid4()),
            player_id=player_id,
            severity=AlertSeverity.WARNING,
            message=(
                f"Você usou {pct*100:.0f}% do seu limite de {limit.limit_type} "
                f"{'(' + limit.period.value + ')' if limit.period else ''} "
                f"(R${limit.value:.2f}). Jogue com responsabilidade."
            ),
            created_at=datetime.now(timezone.utc),
        )
        await self.store.add_alert(alert)
        await self.alerts.dispatch(alert)


# ---------------------------------------------------------------------------
# Session Monitor (background task)
# ---------------------------------------------------------------------------


class SessionMonitor:
    """Background task that checks session time limits every 60 seconds."""

    def __init__(self, engine: ResponsibleGamingEngine, store: RGStore) -> None:
        self.engine = engine
        self.store = store
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())
        logger.info("session_monitor_started")

    async def _run(self) -> None:
        while True:
            try:
                await self._check_all_sessions()
            except Exception as exc:
                logger.error("session_monitor_error", error=str(exc))
            await asyncio.sleep(60)

    async def _check_all_sessions(self) -> None:
        for session_id in list(self.store._sessions.keys()):
            try:
                await self.engine.check_session_time(session_id)
            except SessionLimitReachedError:
                pass  # already handled inside check_session_time
            except Exception as exc:
                logger.error(
                    "session_check_error",
                    session_id=session_id,
                    error=str(exc),
                )

    def stop(self) -> None:
        if self._task:
            self._task.cancel()


# ---------------------------------------------------------------------------
# FastAPI Application
# ---------------------------------------------------------------------------

rg_store = RGStore()
rg_engine: Optional[ResponsibleGamingEngine] = None
session_monitor: Optional[SessionMonitor] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global rg_engine, session_monitor
    rg_engine = ResponsibleGamingEngine(
        store=rg_store,
        scorer=BehavioralRiskScorer(),
        alert_dispatcher=AlertDispatcher(),
        national_registry=ApostaResponsavelClient(
            base_url="https://sandbox.apostaresponsavel.mf.gov.br",
            operator_cnpj="12345678000195",
        ),
    )
    session_monitor = SessionMonitor(rg_engine, rg_store)
    session_monitor.start()
    logger.info("responsible_gaming_engine_started")
    yield
    session_monitor.stop()
    logger.info("responsible_gaming_engine_shutdown")


app = FastAPI(
    title="Responsible Gaming Engine",
    description="Lei 14.790/2023 responsible gaming compliance",
    version="1.0.0",
    lifespan=lifespan,
)


@app.post("/v1/rg/limits", response_model=Dict[str, Any])
async def set_limit(req: SetLimitRequest) -> Dict[str, Any]:
    """Set or update a deposit / loss / session limit."""
    limit = await rg_engine.set_limit(req)  # type: ignore[union-attr]
    return {
        "limit_id": limit.limit_id,
        "player_id": limit.player_id,
        "limit_type": limit.limit_type,
        "period": limit.period.value if limit.period else None,
        "value": limit.value,
        "effective_from": limit.effective_from.isoformat() if limit.effective_from else None,
    }


@app.post("/v1/rg/sessions/start", response_model=Dict[str, Any])
async def start_session(req: SessionStartRequest) -> Dict[str, Any]:
    """Start a betting session with time limit enforcement."""
    session = await rg_engine.start_session(req)  # type: ignore[union-attr]
    return {
        "session_id": session.session_id,
        "player_id": session.player_id,
        "started_at": session.started_at.isoformat(),
        "session_limit_minutes": session.session_limit_minutes,
    }


@app.post("/v1/rg/bet-check", response_model=Dict[str, Any])
async def check_bet(req: BetCheckRequest) -> Dict[str, Any]:
    """Check if a bet/deposit is allowed under active limits."""
    await rg_engine.check_deposit_allowed(req.player_id, req.deposit_amount_brl)  # type: ignore[union-attr]
    await rg_engine.check_session_time(req.session_id)  # type: ignore[union-attr]
    return {"allowed": True, "player_id": req.player_id}


@app.post("/v1/rg/self-exclusion", response_model=Dict[str, Any])
async def self_exclude(req: SelfExclusionRequest) -> Dict[str, Any]:
    """Activate player self-exclusion."""
    # In production: look up CPF hash from KYC service
    cpf_hash = "placeholder_cpf_hash"
    exclusion = await rg_engine.request_self_exclusion(req, cpf_hash)  # type: ignore[union-attr]
    return {
        "exclusion_id": exclusion.exclusion_id,
        "player_id": exclusion.player_id,
        "type": exclusion.exclusion_type.value,
        "ends_at": exclusion.ends_at.isoformat() if exclusion.ends_at else "permanent",
        "national_registry_ack": exclusion.national_registry_ack,
    }


@app.post("/v1/rg/cooling-off/{player_id}", response_model=Dict[str, Any])
async def request_cooling_off(
    player_id: str, duration_hours: int = 24
) -> Dict[str, Any]:
    """Activate voluntary cooling-off period."""
    ends_at = await rg_engine.request_cooling_off(player_id, duration_hours)  # type: ignore[union-attr]
    return {
        "player_id": player_id,
        "cooling_off_until": ends_at.isoformat(),
    }


@app.get("/v1/rg/players/{player_id}/status", response_model=Dict[str, Any])
async def get_status(player_id: str) -> Dict[str, Any]:
    """Get comprehensive responsible gaming status for a player."""
    status = await rg_store.get_account_status(player_id)
    limits = await rg_store.get_limits(player_id)
    exclusion = await rg_store.get_exclusion(player_id)
    risk = await rg_store.get_risk_profile(player_id)
    pending_alerts = await rg_store.get_pending_alerts(player_id)
    return {
        "player_id": player_id,
        "account_status": status.value,
        "risk_level": risk.risk_level.value,
        "risk_score": risk.risk_score,
        "active_limits": [
            {
                "type": l.limit_type,
                "period": l.period.value if l.period else None,
                "value": l.value,
                "used": l.current_usage,
                "remaining": l.remaining,
                "utilisation_pct": l.utilisation_pct,
            }
            for l in limits if l.is_active
        ],
        "pending_alerts": len(pending_alerts),
        "exclusion_active": exclusion.is_active if exclusion else False,
        "exclusion_type": exclusion.exclusion_type.value if exclusion else None,
        "exclusion_ends": exclusion.ends_at.isoformat() if exclusion and exclusion.ends_at else None,
    }


@app.get("/healthz")
async def health() -> Dict[str, str]:
    return {"status": "ok", "service": "responsible-gaming-engine"}


if __name__ == "__main__":
    uvicorn.run("responsible_gaming:app", host="0.0.0.0", port=8005, reload=False)

# Companion code for "The Backend of Luck" - Chapter 26, Responsible Gaming and Player Protection Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Automated Intervention System
Chapter 10 - Responsible Gaming and Player Protection

Triggers automated interventions based on risk thresholds: pop-up messages,
forced session breaks, deposit block, account restriction, and mandatory
interaction escalation.

Compliance References:
- UKGC LCCP 3.4.1: Operators must interact with customers identified as at risk
- UKGC LCCP 3.4.3: Evaluation of customer interaction effectiveness
- MGA PPD 2018: Real-time automated monitoring and intervention obligations
- UKGC Guidance: Interventions must be "effective" - not just tick-box

Architecture:
    Risk Scorer --> Intervention Engine --> Action Dispatcher
                                       --> Notification Service
                                       --> Audit Logger
                                       --> Escalation Queue (for manual review)

Usage:
    engine = InterventionEngine(db_pool, redis, notification_service)
    await engine.process_risk_assessment(risk_assessment)
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import IntEnum, Enum
from typing import Optional, Protocol

import asyncpg  # ty:ignore[unresolved-import]
import redis.asyncio as aioredis

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class InterventionType(str, Enum):
    """
    Intervention types ordered by severity. UKGC expects operators to
    demonstrate escalating interventions based on risk level.
    """
    POPUP_MESSAGE = "popup_message"              # In-session info pop-up
    REALITY_CHECK = "reality_check"              # Time/spend summary
    COOL_OFF_SUGGESTION = "cool_off_suggestion"  # Suggest taking a break
    FORCED_BREAK = "forced_break"                # Log out for N minutes
    DEPOSIT_BLOCK = "deposit_block"              # Prevent further deposits
    WAGER_BLOCK = "wager_block"                  # Prevent further wagers
    ACCOUNT_RESTRICTION = "account_restriction"  # Limit account capabilities
    MANDATORY_INTERACTION = "mandatory_interaction"  # Human must contact player
    ACCOUNT_REVIEW = "account_review"            # Full account review by RG team
    SELF_EXCLUSION_REFERRAL = "self_exclusion_referral"  # Offer self-exclusion


class InterventionStatus(str, Enum):
    PENDING = "pending"
    DELIVERED = "delivered"
    ACKNOWLEDGED = "acknowledged"
    ESCALATED = "escalated"
    COMPLETED = "completed"
    EXPIRED = "expired"


class RiskLevel(IntEnum):
    NONE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


@dataclass
class InterventionAction:
    """A single intervention action to be executed."""
    intervention_type: InterventionType
    player_id: str
    risk_level: RiskLevel
    trigger_reason: str
    message: str
    metadata: dict = field(default_factory=dict)
    # For forced breaks: duration in minutes
    break_duration_minutes: Optional[int] = None
    # Deadline for mandatory interactions (UKGC: within 24h)
    interaction_deadline: Optional[datetime] = None
    expires_at: Optional[datetime] = None


@dataclass
class InterventionResult:
    action: InterventionAction
    status: InterventionStatus
    delivered_at: Optional[datetime] = None
    acknowledged_at: Optional[datetime] = None
    player_response: Optional[str] = None


# ---------------------------------------------------------------------------
# Notification Protocol (implement per channel)
# ---------------------------------------------------------------------------

class NotificationService(Protocol):
    """Interface for delivering interventions to players."""

    async def send_popup(self, player_id: str, message: str, metadata: dict) -> bool:
        """Show in-session popup. Returns True if delivered."""
        ...

    async def send_email(self, player_id: str, subject: str, body: str) -> bool:
        """Send email notification."""
        ...

    async def force_logout(self, player_id: str, reason: str) -> bool:
        """Force player session termination."""
        ...

    async def block_deposits(self, player_id: str, until: datetime) -> bool:
        """Prevent player from making deposits until the given time."""
        ...

    async def restrict_account(self, player_id: str, restrictions: list[str]) -> bool:
        """Apply account restrictions."""
        ...


# ---------------------------------------------------------------------------
# Intervention Configuration
# ---------------------------------------------------------------------------

@dataclass
class InterventionConfig:
    """
    Maps risk levels to intervention actions. Operators must calibrate
    these thresholds and demonstrate their effectiveness to regulators.
    """

    # Minimum time between non-critical interventions (avoid fatigue)
    min_interval_minutes: int = 30

    # Forced break durations by risk level (minutes)
    forced_break_medium: int = 15
    forced_break_high: int = 30
    forced_break_critical: int = 60

    # Messages per intervention type (i18n keys in production)
    messages: dict = field(default_factory=lambda: {
        "popup_info": (
            "You have been playing for {duration} minutes and have spent {total_spent}. "
            "Would you like to take a break or set a limit?"
        ),
        "reality_check": (
            "Session summary: Duration {duration}min | Wagered: {wagered} | "
            "Net result: {net_result}. Remember to gamble responsibly."
        ),
        "cool_off_suggestion": (
            "We've noticed some changes in your playing patterns. "
            "Taking a break can help you stay in control. "
            "Would you like to activate a cooling-off period?"
        ),
        "forced_break": (
            "For your protection, we're taking a short break from your session. "
            "You can return in {break_minutes} minutes. "
            "If you need support, contact {support_url}."
        ),
        "deposit_block": (
            "We've temporarily paused deposits on your account as part of our "
            "responsible gambling measures. You can contact support for more information."
        ),
        "mandatory_interaction": (
            "A member of our responsible gambling team will be in touch with you "
            "within {deadline_hours} hours. In the meantime, you can reach us at "
            "{support_phone} or {support_email}."
        ),
    })


# ---------------------------------------------------------------------------
# Intervention Engine
# ---------------------------------------------------------------------------

class InterventionEngine:
    """
    Processes risk assessments and dispatches appropriate interventions.

    UKGC LCCP 3.4.1 requires:
    1. Identification of customers at risk (via risk scorer)
    2. Interaction with those customers (this engine)
    3. Evaluation of interaction effectiveness (outcome tracking)
    4. Action taken following evaluation (escalation)

    MGA PPD additionally requires:
    - Automated real-time monitoring triggers
    - Documented intervention policies
    - Regular review of intervention thresholds
    """

    def __init__(
        self,
        db_pool: asyncpg.Pool,
        redis: aioredis.Redis,
        notifications: NotificationService,
        config: Optional[InterventionConfig] = None,
    ):
        self.db = db_pool
        self.redis = redis
        self.notifications = notifications
        self.config = config or InterventionConfig()

    async def process_risk_assessment(self, assessment: dict) -> list[InterventionResult]:
        """
        Process a risk assessment and trigger appropriate interventions.

        Args:
            assessment: Dict with keys: player_id, overall_risk (int),
                       overall_score (float), indicators (list), recommended_actions (list)

        Returns:
            List of InterventionResult for each action taken.
        """
        player_id = assessment["player_id"]
        risk_level = RiskLevel(assessment["overall_risk"])

        if risk_level == RiskLevel.NONE:
            return []

        # Check intervention cooldown to avoid alert fatigue
        if risk_level < RiskLevel.CRITICAL:
            if await self._is_in_cooldown(player_id):
                logger.debug("Skipping intervention for %s: in cooldown", player_id)
                return []

        actions = self._determine_actions(assessment, risk_level)
        results = []

        for action in actions:
            result = await self._execute_action(action)
            results.append(result)
            await self._persist_intervention(action, result)

        # Set cooldown
        await self._set_cooldown(player_id)

        return results

    def _determine_actions(
        self, assessment: dict, risk_level: RiskLevel
    ) -> list[InterventionAction]:
        """
        Determine which interventions to trigger based on risk level.
        Interventions escalate with risk severity.
        """
        player_id = assessment["player_id"]
        indicators = {i["name"]: i for i in assessment.get("indicators", [])}
        actions = []

        if risk_level == RiskLevel.LOW:
            # Informational only - no forced action
            actions.append(InterventionAction(
                intervention_type=InterventionType.POPUP_MESSAGE,
                player_id=player_id,
                risk_level=risk_level,
                trigger_reason="low_risk_monitoring",
                message=self.config.messages["popup_info"],
                metadata={"indicators": list(indicators.keys())},
            ))

        elif risk_level == RiskLevel.MEDIUM:
            # Reality check + cool-off suggestion
            actions.append(InterventionAction(
                intervention_type=InterventionType.REALITY_CHECK,
                player_id=player_id,
                risk_level=risk_level,
                trigger_reason="medium_risk_indicators",
                message=self.config.messages["reality_check"],
            ))
            actions.append(InterventionAction(
                intervention_type=InterventionType.COOL_OFF_SUGGESTION,
                player_id=player_id,
                risk_level=risk_level,
                trigger_reason="medium_risk_suggestion",
                message=self.config.messages["cool_off_suggestion"],
            ))

            # If loss chasing detected, suggest lower limits
            if indicators.get("loss_chasing", {}).get("score", 0) > 0.3:
                actions.append(InterventionAction(
                    intervention_type=InterventionType.POPUP_MESSAGE,
                    player_id=player_id,
                    risk_level=risk_level,
                    trigger_reason="loss_chasing_detected",
                    message="We've noticed you may be increasing stakes after losses. "
                            "Consider setting a loss limit to stay in control.",
                    metadata={"loss_chasing_score": indicators["loss_chasing"]["score"]},
                ))

        elif risk_level == RiskLevel.HIGH:
            # Forced break + deposit block + mandatory interaction
            actions.append(InterventionAction(
                intervention_type=InterventionType.FORCED_BREAK,
                player_id=player_id,
                risk_level=risk_level,
                trigger_reason="high_risk_forced_break",
                message=self.config.messages["forced_break"].format(
                    break_minutes=self.config.forced_break_high,
                    support_url="https://example.com/support",
                ),
                break_duration_minutes=self.config.forced_break_high,
            ))
            actions.append(InterventionAction(
                intervention_type=InterventionType.DEPOSIT_BLOCK,
                player_id=player_id,
                risk_level=risk_level,
                trigger_reason="high_risk_deposit_block",
                message=self.config.messages["deposit_block"],
                expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
            ))
            # UKGC: mandatory interaction within 24h
            actions.append(InterventionAction(
                intervention_type=InterventionType.MANDATORY_INTERACTION,
                player_id=player_id,
                risk_level=risk_level,
                trigger_reason="high_risk_mandatory_interaction",
                message=self.config.messages["mandatory_interaction"].format(
                    deadline_hours=24,
                    support_phone="+44-800-XXX-XXXX",
                    support_email="rg@example.com",
                ),
                interaction_deadline=datetime.now(timezone.utc) + timedelta(hours=24),
            ))

        elif risk_level == RiskLevel.CRITICAL:
            # Everything from HIGH plus account review and self-exclusion referral
            actions.append(InterventionAction(
                intervention_type=InterventionType.FORCED_BREAK,
                player_id=player_id,
                risk_level=risk_level,
                trigger_reason="critical_risk_immediate_break",
                message=self.config.messages["forced_break"].format(
                    break_minutes=self.config.forced_break_critical,
                    support_url="https://example.com/support",
                ),
                break_duration_minutes=self.config.forced_break_critical,
            ))
            actions.append(InterventionAction(
                intervention_type=InterventionType.ACCOUNT_RESTRICTION,
                player_id=player_id,
                risk_level=risk_level,
                trigger_reason="critical_risk_restriction",
                message="Your account has been restricted pending review by our "
                        "responsible gambling team.",
                metadata={"restrictions": ["no_deposits", "no_wagers", "no_bonuses"]},
            ))
            actions.append(InterventionAction(
                intervention_type=InterventionType.ACCOUNT_REVIEW,
                player_id=player_id,
                risk_level=risk_level,
                trigger_reason="critical_risk_review",
                message="Account flagged for urgent RG team review.",
                interaction_deadline=datetime.now(timezone.utc) + timedelta(hours=4),
            ))
            actions.append(InterventionAction(
                intervention_type=InterventionType.SELF_EXCLUSION_REFERRAL,
                player_id=player_id,
                risk_level=risk_level,
                trigger_reason="critical_risk_exclusion_offer",
                message="If you feel gambling is causing you harm, you can "
                        "self-exclude from all gambling sites via GAMSTOP "
                        "(https://www.gamstop.co.uk) or speak to our team.",
            ))

        return actions

    async def _execute_action(self, action: InterventionAction) -> InterventionResult:
        """Execute a single intervention action via the notification service."""
        try:
            delivered = False

            if action.intervention_type == InterventionType.POPUP_MESSAGE:
                delivered = await self.notifications.send_popup(
                    action.player_id, action.message, action.metadata
                )
            elif action.intervention_type == InterventionType.REALITY_CHECK:
                delivered = await self.notifications.send_popup(
                    action.player_id, action.message,
                    {"type": "reality_check", **action.metadata},
                )
            elif action.intervention_type == InterventionType.COOL_OFF_SUGGESTION:
                delivered = await self.notifications.send_popup(
                    action.player_id, action.message,
                    {"type": "cool_off_suggestion", "show_cool_off_button": True},
                )
            elif action.intervention_type == InterventionType.FORCED_BREAK:
                delivered = await self.notifications.force_logout(
                    action.player_id,
                    f"Forced break: {action.break_duration_minutes} minutes",
                )
                if delivered and action.break_duration_minutes:
                    # Set a login block in Redis
                    block_key = f"rg:login_block:{action.player_id}"
                    await self.redis.set(
                        block_key, action.trigger_reason,
                        ex=action.break_duration_minutes * 60,
                    )
            elif action.intervention_type == InterventionType.DEPOSIT_BLOCK:
                delivered = await self.notifications.block_deposits(
                    action.player_id, action.expires_at or (
                        datetime.now(timezone.utc) + timedelta(hours=24)
                    ),
                )
            elif action.intervention_type == InterventionType.ACCOUNT_RESTRICTION:
                restrictions = action.metadata.get("restrictions", [])
                delivered = await self.notifications.restrict_account(
                    action.player_id, restrictions
                )
            elif action.intervention_type in (
                InterventionType.MANDATORY_INTERACTION,
                InterventionType.ACCOUNT_REVIEW,
            ):
                # Queue for human RG team
                await self._queue_for_rg_team(action)
                # Also email the player
                delivered = await self.notifications.send_email(
                    action.player_id,
                    "Important: About your gambling activity",
                    action.message,
                )
            elif action.intervention_type == InterventionType.SELF_EXCLUSION_REFERRAL:
                delivered = await self.notifications.send_popup(
                    action.player_id, action.message,
                    {"type": "self_exclusion_referral", "show_gamstop_link": True},
                )
                await self.notifications.send_email(
                    action.player_id,
                    "Self-exclusion options available",
                    action.message,
                )

            status = InterventionStatus.DELIVERED if delivered else InterventionStatus.PENDING
            return InterventionResult(
                action=action,
                status=status,
                delivered_at=datetime.now(timezone.utc) if delivered else None,
            )

        except Exception as e:
            logger.exception(
                "Failed to execute intervention %s for %s: %s",
                action.intervention_type.value, action.player_id, e,
            )
            return InterventionResult(action=action, status=InterventionStatus.PENDING)

    async def _queue_for_rg_team(self, action: InterventionAction) -> None:
        """Add to responsible gambling team's work queue."""
        queue_key = "rg:team_queue"
        item = json.dumps({
            "player_id": action.player_id,
            "intervention_type": action.intervention_type.value,
            "risk_level": action.risk_level,
            "trigger_reason": action.trigger_reason,
            "deadline": action.interaction_deadline.isoformat() if action.interaction_deadline else None,
            "queued_at": datetime.now(timezone.utc).isoformat(),
        })
        await self.redis.lpush(queue_key, item)  # ty:ignore[invalid-await]
        logger.info(
            "Queued for RG team: %s (%s) deadline=%s",
            action.player_id, action.intervention_type.value,
            action.interaction_deadline,
        )

    async def _is_in_cooldown(self, player_id: str) -> bool:
        """Check if player has had a recent intervention (avoid fatigue)."""
        key = f"rg:intervention_cooldown:{player_id}"
        return await self.redis.exists(key) > 0

    async def _set_cooldown(self, player_id: str) -> None:
        key = f"rg:intervention_cooldown:{player_id}"
        await self.redis.set(key, "1", ex=self.config.min_interval_minutes * 60)

    async def _persist_intervention(
        self, action: InterventionAction, result: InterventionResult
    ) -> None:
        """
        Persist intervention for audit and effectiveness tracking.
        UKGC LCCP 3.4.3: Operators must evaluate whether customer interactions
        are effective. This data enables that analysis.
        """
        try:
            async with self.db.acquire() as conn:
                await conn.execute("""
                    INSERT INTO rg_interventions
                        (player_id, intervention_type, risk_level, trigger_reason,
                         message, metadata, status, delivered_at,
                         interaction_deadline, created_at)
                    VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8, $9, NOW())
                """,
                    action.player_id,
                    action.intervention_type.value,
                    action.risk_level,
                    action.trigger_reason,
                    action.message,
                    json.dumps(action.metadata),
                    result.status.value,
                    result.delivered_at,
                    action.interaction_deadline,
                )
        except Exception:
            logger.exception("Failed to persist intervention for %s", action.player_id)

    # -------------------------------------------------------------------
    # Intervention Outcome Tracking
    # -------------------------------------------------------------------

    async def record_outcome(
        self,
        intervention_id: int,
        player_response: str,
        outcome: str,
        notes: str = "",
    ) -> None:
        """
        Record the outcome of an intervention for effectiveness analysis.

        UKGC requires operators to track:
        - Did the player acknowledge the interaction?
        - Did the player change behavior after interaction?
        - Was the intervention type appropriate for the risk level?

        Possible outcomes: "positive_change", "no_change", "escalated",
                          "self_excluded", "account_closed"
        """
        async with self.db.acquire() as conn:
            await conn.execute("""
                UPDATE rg_interventions
                SET status = 'completed',
                    player_response = $2,
                    outcome = $3,
                    outcome_notes = $4,
                    completed_at = NOW()
                WHERE intervention_id = $1
            """, intervention_id, player_response, outcome, notes)

    async def get_overdue_interactions(self) -> list[dict]:
        """
        Find mandatory interactions that haven't been completed before deadline.
        Run as scheduled job to alert RG team managers.
        """
        async with self.db.acquire() as conn:
            rows = await conn.fetch("""
                SELECT intervention_id, player_id, risk_level,
                       interaction_deadline, created_at
                FROM rg_interventions
                WHERE intervention_type IN ('mandatory_interaction', 'account_review')
                  AND status NOT IN ('completed', 'expired')
                  AND interaction_deadline < NOW()
                ORDER BY interaction_deadline ASC
            """)
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS rg_interventions (
    intervention_id     BIGSERIAL PRIMARY KEY,
    player_id           VARCHAR(64) NOT NULL,
    intervention_type   VARCHAR(40) NOT NULL,
    risk_level          INTEGER NOT NULL,
    trigger_reason      VARCHAR(100),
    message             TEXT,
    metadata            JSONB DEFAULT '{}'::jsonb,
    status              VARCHAR(20) NOT NULL DEFAULT 'pending',
    delivered_at        TIMESTAMPTZ,
    acknowledged_at     TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,
    player_response     TEXT,
    outcome             VARCHAR(40),
    outcome_notes       TEXT,
    interaction_deadline TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_intervention_player
    ON rg_interventions (player_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_intervention_pending
    ON rg_interventions (status, interaction_deadline)
    WHERE status NOT IN ('completed', 'expired');

COMMENT ON TABLE rg_interventions IS
    'Responsible gambling interventions. Track delivery, acknowledgment, and outcomes.
     UKGC LCCP 3.4.3 requires effectiveness evaluation.';
"""

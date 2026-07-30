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
Player Risk Assessment Engine
Chapter 10 - Responsible Gaming and Player Protection

Production-grade risk scoring engine with configurable indicators for detecting
problem gambling patterns. Covers session duration analysis, deposit velocity,
loss chasing behavior, time-of-day anomalies, and composite risk scoring.

Compliance References:
- UKGC LCCP Social Responsibility Code 3.4.1: Customer interaction requirements
- MGA Player Protection Directive (PPD/2018): Risk-based monitoring obligations
- UKGC Guidance: "Customer interaction - formal guidance" (2019, updated 2022)
- MGA Directive 2 of 2018: Player risk profiling framework

Usage:
    scorer = PlayerRiskScorer(config=RiskConfig())
    risk_result = await scorer.evaluate_player("player_12345")
    if risk_result.overall_risk >= RiskLevel.HIGH:
        await trigger_intervention(risk_result)
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import IntEnum
from typing import Optional

import asyncpg  # ty:ignore[unresolved-import]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration & Enums
# ---------------------------------------------------------------------------

class RiskLevel(IntEnum):
    """Risk levels aligned with UKGC customer interaction guidance."""
    NONE = 0
    LOW = 1       # Monitor, no action required
    MEDIUM = 2    # Proactive check-in recommended
    HIGH = 3      # Mandatory interaction within 24h (UKGC LCCP 3.4.1)
    CRITICAL = 4  # Immediate intervention required


@dataclass
class RiskConfig:
    """
    Configurable risk thresholds. Operators MUST calibrate these per their
    player base demographics and regulatory jurisdiction.

    UKGC expects operators to demonstrate they have set "meaningful thresholds"
    and can justify them during licence reviews.
    """

    # --- Session duration indicators ---
    session_warning_minutes: int = 60       # Reality check trigger
    session_risk_minutes: int = 180         # 3h continuous play
    session_critical_minutes: int = 360     # 6h continuous play

    # --- Deposit velocity (rolling 24h window) ---
    deposit_count_medium: int = 3           # 3+ deposits in 24h
    deposit_count_high: int = 5             # 5+ deposits in 24h
    deposit_amount_spike_factor: float = 3.0  # 3x above 30-day average

    # --- Loss chasing detection ---
    # Player increases stake by this factor after a loss streak
    loss_chase_stake_increase: float = 2.0
    # Minimum consecutive losses before checking for chasing
    loss_chase_min_streak: int = 3
    # Rapid re-deposit after loss (minutes)
    rapid_redeposit_minutes: int = 10

    # --- Time-of-day patterns ---
    # Sessions starting between these hours flag for review
    # UKGC research: late-night play correlates with problem gambling
    risky_hours_start: int = 2   # 02:00
    risky_hours_end: int = 5     # 05:00
    late_night_session_count_threshold: int = 3  # 3+ sessions in 7 days

    # --- Composite scoring weights (must sum to 1.0) ---
    weight_session_duration: float = 0.20
    weight_deposit_velocity: float = 0.25
    weight_loss_chasing: float = 0.25
    weight_time_of_day: float = 0.10
    weight_stake_escalation: float = 0.10
    weight_withdrawal_reversal: float = 0.10

    # --- Composite thresholds ---
    composite_medium: float = 0.35
    composite_high: float = 0.55
    composite_critical: float = 0.75

    def validate(self):
        weights = [
            self.weight_session_duration,
            self.weight_deposit_velocity,
            self.weight_loss_chasing,
            self.weight_time_of_day,
            self.weight_stake_escalation,
            self.weight_withdrawal_reversal,
        ]
        total = sum(weights)
        if abs(total - 1.0) > 0.001:
            raise ValueError(f"Risk weights must sum to 1.0, got {total:.4f}")


@dataclass
class IndicatorResult:
    """Result of a single risk indicator evaluation."""
    name: str
    score: float           # 0.0 to 1.0
    risk_level: RiskLevel
    details: dict = field(default_factory=dict)
    triggered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class RiskAssessment:
    """Complete risk assessment for a player."""
    player_id: str
    overall_score: float
    overall_risk: RiskLevel
    indicators: list[IndicatorResult]
    assessed_at: datetime
    requires_interaction: bool = False
    interaction_deadline: Optional[datetime] = None
    recommended_actions: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Database Queries
# ---------------------------------------------------------------------------

QUERIES = {
    "active_session": """
        SELECT session_id, started_at, total_wagered, total_won, total_lost,
               game_type, device_type
        FROM player_sessions
        WHERE player_id = $1 AND ended_at IS NULL
        ORDER BY started_at DESC LIMIT 1
    """,

    "recent_sessions_7d": """
        SELECT session_id, started_at, ended_at,
               EXTRACT(EPOCH FROM (COALESCE(ended_at, NOW()) - started_at)) / 60 AS duration_minutes,
               total_wagered, total_lost
        FROM player_sessions
        WHERE player_id = $1
          AND started_at >= NOW() - INTERVAL '7 days'
        ORDER BY started_at DESC
    """,

    "deposits_24h": """
        SELECT deposit_id, amount, currency, created_at, payment_method
        FROM player_deposits
        WHERE player_id = $1
          AND status = 'completed'
          AND created_at >= NOW() - INTERVAL '24 hours'
        ORDER BY created_at DESC
    """,

    "deposit_avg_30d": """
        SELECT COALESCE(AVG(daily_total), 0) AS avg_daily_deposit
        FROM (
            SELECT DATE(created_at) AS deposit_date, SUM(amount) AS daily_total
            FROM player_deposits
            WHERE player_id = $1
              AND status = 'completed'
              AND created_at >= NOW() - INTERVAL '30 days'
            GROUP BY DATE(created_at)
        ) daily
    """,

    "recent_bets": """
        SELECT bet_id, stake, potential_win, result, settled_at, game_type
        FROM player_bets
        WHERE player_id = $1
          AND settled_at >= NOW() - INTERVAL '24 hours'
        ORDER BY settled_at DESC
        LIMIT 200
    """,

    "withdrawal_reversals_30d": """
        SELECT COUNT(*) AS reversal_count
        FROM player_withdrawals
        WHERE player_id = $1
          AND status = 'reversed'
          AND updated_at >= NOW() - INTERVAL '30 days'
    """,

    "save_assessment": """
        INSERT INTO player_risk_assessments
            (player_id, overall_score, overall_risk, indicators_json,
             requires_interaction, interaction_deadline, recommended_actions,
             assessed_at)
        VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8)
        RETURNING assessment_id
    """,
}


# ---------------------------------------------------------------------------
# Risk Scorer
# ---------------------------------------------------------------------------

class PlayerRiskScorer:
    """
    Evaluates player risk across multiple behavioral indicators.

    Architecture:
        1. Each indicator is evaluated independently (0.0 to 1.0 score)
        2. Weighted composite score determines overall risk level
        3. Results are persisted for audit trail (UKGC requirement)
        4. High/Critical triggers are forwarded to intervention system

    UKGC LCCP 3.4.1 requires:
        - Operators must interact with customers showing signs of harm
        - Interactions must be timely and effective
        - Records must be kept of all interactions and outcomes
    """

    def __init__(self, db_pool: asyncpg.Pool, config: Optional[RiskConfig] = None):
        self.db = db_pool
        self.config = config or RiskConfig()
        self.config.validate()

    async def evaluate_player(self, player_id: str) -> RiskAssessment:
        """
        Run full risk assessment for a player. Returns a RiskAssessment
        with individual indicator scores and an overall composite score.
        """
        indicators = await asyncio.gather(
            self._evaluate_session_duration(player_id),
            self._evaluate_deposit_velocity(player_id),
            self._evaluate_loss_chasing(player_id),
            self._evaluate_time_of_day(player_id),
            self._evaluate_stake_escalation(player_id),
            self._evaluate_withdrawal_reversals(player_id),
        )

        # Compute weighted composite score
        weights = [
            self.config.weight_session_duration,
            self.config.weight_deposit_velocity,
            self.config.weight_loss_chasing,
            self.config.weight_time_of_day,
            self.config.weight_stake_escalation,
            self.config.weight_withdrawal_reversal,
        ]
        composite = sum(ind.score * w for ind, w in zip(indicators, weights))

        # Determine overall risk level
        if composite >= self.config.composite_critical:
            overall_risk = RiskLevel.CRITICAL
        elif composite >= self.config.composite_high:
            overall_risk = RiskLevel.HIGH
        elif composite >= self.config.composite_medium:
            overall_risk = RiskLevel.MEDIUM
        elif composite > 0:
            overall_risk = RiskLevel.LOW
        else:
            overall_risk = RiskLevel.NONE

        # UKGC requires interaction within 24h for HIGH risk
        requires_interaction = overall_risk >= RiskLevel.HIGH
        interaction_deadline = None
        if requires_interaction:
            interaction_deadline = datetime.now(timezone.utc) + timedelta(hours=24)

        recommended_actions = self._build_recommendations(indicators, overall_risk)

        assessment = RiskAssessment(
            player_id=player_id,
            overall_score=round(composite, 4),
            overall_risk=overall_risk,
            indicators=list(indicators),
            assessed_at=datetime.now(timezone.utc),
            requires_interaction=requires_interaction,
            interaction_deadline=interaction_deadline,
            recommended_actions=recommended_actions,
        )

        await self._persist_assessment(assessment)

        logger.info(
            "Risk assessment completed",
            extra={
                "player_id": player_id,
                "overall_score": assessment.overall_score,
                "overall_risk": assessment.overall_risk.name,
                "requires_interaction": requires_interaction,
            },
        )

        return assessment

    # -------------------------------------------------------------------
    # Individual indicator evaluators
    # -------------------------------------------------------------------

    async def _evaluate_session_duration(self, player_id: str) -> IndicatorResult:
        """
        Assess risk from session duration. Prolonged uninterrupted play
        is a strong indicator of problem gambling (Griffiths, 2012).
        """
        async with self.db.acquire() as conn:
            session = await conn.fetchrow(QUERIES["active_session"], player_id)

        if not session:
            return IndicatorResult(
                name="session_duration", score=0.0, risk_level=RiskLevel.NONE,
                details={"status": "no_active_session"},
            )

        started_at = session["started_at"]
        duration_min = (datetime.now(timezone.utc) - started_at).total_seconds() / 60

        if duration_min >= self.config.session_critical_minutes:
            score, level = 1.0, RiskLevel.CRITICAL
        elif duration_min >= self.config.session_risk_minutes:
            score = 0.5 + 0.5 * (
                (duration_min - self.config.session_risk_minutes)
                / (self.config.session_critical_minutes - self.config.session_risk_minutes)
            )
            level = RiskLevel.HIGH
        elif duration_min >= self.config.session_warning_minutes:
            score = 0.2 + 0.3 * (
                (duration_min - self.config.session_warning_minutes)
                / (self.config.session_risk_minutes - self.config.session_warning_minutes)
            )
            level = RiskLevel.MEDIUM
        else:
            score = duration_min / self.config.session_warning_minutes * 0.2
            level = RiskLevel.LOW if duration_min > 30 else RiskLevel.NONE

        return IndicatorResult(
            name="session_duration",
            score=min(score, 1.0),
            risk_level=level,
            details={
                "duration_minutes": round(duration_min, 1),
                "session_id": str(session["session_id"]),
                "game_type": session["game_type"],
                "total_lost": float(session["total_lost"]),
            },
        )

    async def _evaluate_deposit_velocity(self, player_id: str) -> IndicatorResult:
        """
        Assess deposit frequency and amount spikes. Rapid, escalating deposits
        are a key harm indicator per UKGC customer interaction guidance.
        """
        async with self.db.acquire() as conn:
            deposits_24h = await conn.fetch(QUERIES["deposits_24h"], player_id)
            avg_row = await conn.fetchrow(QUERIES["deposit_avg_30d"], player_id)

        deposit_count = len(deposits_24h)
        total_24h = sum(float(d["amount"]) for d in deposits_24h)
        avg_daily = float(avg_row["avg_daily_deposit"]) if avg_row else 0.0

        score = 0.0
        details = {
            "deposit_count_24h": deposit_count,
            "total_amount_24h": total_24h,
            "avg_daily_30d": round(avg_daily, 2),
        }

        # Count-based scoring
        if deposit_count >= self.config.deposit_count_high:
            score += 0.5
        elif deposit_count >= self.config.deposit_count_medium:
            score += 0.3

        # Amount spike scoring
        if avg_daily > 0 and total_24h > avg_daily * self.config.deposit_amount_spike_factor:
            spike_ratio = total_24h / avg_daily
            score += min(0.5, spike_ratio / 10.0)
            details["spike_ratio"] = round(spike_ratio, 2)

        # Rapid re-deposit detection (deposit within N minutes of a loss)
        if len(deposits_24h) >= 2:
            rapid_count = 0
            for i in range(1, len(deposits_24h)):
                gap = (deposits_24h[i - 1]["created_at"] - deposits_24h[i]["created_at"])
                if gap.total_seconds() / 60 <= self.config.rapid_redeposit_minutes:
                    rapid_count += 1
            if rapid_count > 0:
                score += 0.2
                details["rapid_redeposits"] = rapid_count

        score = min(score, 1.0)
        level = self._score_to_level(score)

        return IndicatorResult(
            name="deposit_velocity", score=score, risk_level=level, details=details,
        )

    async def _evaluate_loss_chasing(self, player_id: str) -> IndicatorResult:
        """
        Detect loss chasing behavior: increasing stakes after consecutive losses.
        This is one of the strongest predictors of problem gambling (Breen & Zuckerman, 1999).
        """
        async with self.db.acquire() as conn:
            bets = await conn.fetch(QUERIES["recent_bets"], player_id)

        if len(bets) < self.config.loss_chase_min_streak:
            return IndicatorResult(
                name="loss_chasing", score=0.0, risk_level=RiskLevel.NONE,
                details={"bet_count": len(bets)},
            )

        # Analyze consecutive loss sequences
        loss_streaks = []
        current_streak = []
        for bet in bets:
            if bet["result"] == "loss":
                current_streak.append(float(bet["stake"]))
            else:
                if len(current_streak) >= self.config.loss_chase_min_streak:
                    loss_streaks.append(current_streak)
                current_streak = []
        if len(current_streak) >= self.config.loss_chase_min_streak:
            loss_streaks.append(current_streak)

        if not loss_streaks:
            return IndicatorResult(
                name="loss_chasing", score=0.0, risk_level=RiskLevel.NONE,
                details={"loss_streaks_found": 0},
            )

        # Check for stake escalation within streaks
        chasing_score = 0.0
        chase_events = 0
        for streak in loss_streaks:
            for i in range(1, len(streak)):
                if streak[i] >= streak[i - 1] * self.config.loss_chase_stake_increase:
                    chase_events += 1

        if chase_events > 0:
            chasing_score = min(1.0, chase_events * 0.25)

        # Also score based on longest loss streak length
        max_streak = max(len(s) for s in loss_streaks)
        streak_score = min(0.5, (max_streak - self.config.loss_chase_min_streak) * 0.1)
        score = min(1.0, chasing_score + streak_score)

        return IndicatorResult(
            name="loss_chasing",
            score=score,
            risk_level=self._score_to_level(score),
            details={
                "loss_streaks_found": len(loss_streaks),
                "chase_events": chase_events,
                "max_streak_length": max_streak,
            },
        )

    async def _evaluate_time_of_day(self, player_id: str) -> IndicatorResult:
        """
        Flag sessions during high-risk hours (typically 02:00-05:00).
        UKGC research associates late-night gambling with higher harm risk.
        """
        async with self.db.acquire() as conn:
            sessions = await conn.fetch(QUERIES["recent_sessions_7d"], player_id)

        late_night_sessions = []
        for s in sessions:
            hour = s["started_at"].hour
            if self.config.risky_hours_start <= hour < self.config.risky_hours_end:
                late_night_sessions.append(s)

        count = len(late_night_sessions)
        if count >= self.config.late_night_session_count_threshold:
            score = min(1.0, count / (self.config.late_night_session_count_threshold * 2))
        elif count > 0:
            score = count / self.config.late_night_session_count_threshold * 0.4
        else:
            score = 0.0

        return IndicatorResult(
            name="time_of_day",
            score=score,
            risk_level=self._score_to_level(score),
            details={
                "late_night_sessions_7d": count,
                "total_sessions_7d": len(sessions),
            },
        )

    async def _evaluate_stake_escalation(self, player_id: str) -> IndicatorResult:
        """
        Detect progressive stake increases independent of loss chasing.
        A player whose average stake doubles week-over-week may be at risk.
        """
        async with self.db.acquire() as conn:
            bets = await conn.fetch(QUERIES["recent_bets"], player_id)

        if len(bets) < 10:
            return IndicatorResult(
                name="stake_escalation", score=0.0, risk_level=RiskLevel.NONE,
                details={"insufficient_data": True},
            )

        stakes = [float(b["stake"]) for b in bets]
        # Compare first half average vs second half (recent)
        midpoint = len(stakes) // 2
        older_avg = sum(stakes[midpoint:]) / len(stakes[midpoint:])
        recent_avg = sum(stakes[:midpoint]) / len(stakes[:midpoint])

        if older_avg <= 0:
            return IndicatorResult(
                name="stake_escalation", score=0.0, risk_level=RiskLevel.NONE,
                details={"older_avg": 0},
            )

        escalation_ratio = recent_avg / older_avg
        if escalation_ratio >= 3.0:
            score = 1.0
        elif escalation_ratio >= 2.0:
            score = 0.6
        elif escalation_ratio >= 1.5:
            score = 0.3
        else:
            score = 0.0

        return IndicatorResult(
            name="stake_escalation",
            score=score,
            risk_level=self._score_to_level(score),
            details={
                "older_avg_stake": round(older_avg, 2),
                "recent_avg_stake": round(recent_avg, 2),
                "escalation_ratio": round(escalation_ratio, 2),
            },
        )

    async def _evaluate_withdrawal_reversals(self, player_id: str) -> IndicatorResult:
        """
        Track withdrawal cancellations/reversals. Players who repeatedly
        cancel pending withdrawals to continue gambling are at elevated risk.
        MGA PPD requires monitoring of withdrawal reversal patterns.
        """
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(QUERIES["withdrawal_reversals_30d"], player_id)

        reversal_count = row["reversal_count"] if row else 0

        if reversal_count >= 5:
            score = 1.0
        elif reversal_count >= 3:
            score = 0.6
        elif reversal_count >= 1:
            score = 0.3
        else:
            score = 0.0

        return IndicatorResult(
            name="withdrawal_reversals",
            score=score,
            risk_level=self._score_to_level(score),
            details={"reversal_count_30d": reversal_count},
        )

    # -------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------

    def _score_to_level(self, score: float) -> RiskLevel:
        if score >= 0.75:
            return RiskLevel.CRITICAL
        elif score >= 0.55:
            return RiskLevel.HIGH
        elif score >= 0.35:
            return RiskLevel.MEDIUM
        elif score > 0:
            return RiskLevel.LOW
        return RiskLevel.NONE

    def _build_recommendations(
        self, indicators: tuple[IndicatorResult, ...], overall: RiskLevel
    ) -> list[str]:
        actions = []
        ind_map = {i.name: i for i in indicators}

        if overall >= RiskLevel.CRITICAL:
            actions.append("MANDATORY: Initiate immediate customer interaction")
            actions.append("MANDATORY: Review account for potential self-exclusion referral")

        if overall >= RiskLevel.HIGH:
            actions.append("Send responsible gambling messaging within 24 hours")
            actions.append("Flag account for manual review by RG team")

        sd = ind_map.get("session_duration")
        if sd and sd.risk_level >= RiskLevel.HIGH:
            actions.append(f"Player in session for {sd.details.get('duration_minutes', '?')} min - trigger reality check")

        dv = ind_map.get("deposit_velocity")
        if dv and dv.risk_level >= RiskLevel.MEDIUM:
            actions.append("Review deposit limits; suggest player sets lower limit")

        lc = ind_map.get("loss_chasing")
        if lc and lc.risk_level >= RiskLevel.MEDIUM:
            actions.append("Loss chasing detected - consider cooling-off suggestion")

        wr = ind_map.get("withdrawal_reversals")
        if wr and wr.risk_level >= RiskLevel.MEDIUM:
            actions.append("Review withdrawal reversal pattern - consider removing reversal option")

        return actions

    async def _persist_assessment(self, assessment: RiskAssessment) -> None:
        """
        Save assessment for audit trail.
        UKGC LCCP 3.4.1: Licensees must keep records of customer interactions
        and the outcomes for a minimum of 3 years.
        """
        import json

        indicators_json = json.dumps([
            {
                "name": ind.name,
                "score": ind.score,
                "risk_level": ind.risk_level.name,
                "details": ind.details,
                "triggered_at": ind.triggered_at.isoformat(),
            }
            for ind in assessment.indicators
        ])

        try:
            async with self.db.acquire() as conn:
                await conn.fetchval(
                    QUERIES["save_assessment"],
                    assessment.player_id,
                    assessment.overall_score,
                    assessment.overall_risk.name,
                    indicators_json,
                    assessment.requires_interaction,
                    assessment.interaction_deadline,
                    assessment.recommended_actions,
                    assessment.assessed_at,
                )
        except Exception:
            logger.exception("Failed to persist risk assessment for %s", assessment.player_id)


# ---------------------------------------------------------------------------
# Schema migration (run once)
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS player_risk_assessments (
    assessment_id   BIGSERIAL PRIMARY KEY,
    player_id       VARCHAR(64) NOT NULL,
    overall_score   NUMERIC(6,4) NOT NULL,
    overall_risk    VARCHAR(16) NOT NULL,
    indicators_json JSONB NOT NULL,
    requires_interaction BOOLEAN DEFAULT FALSE,
    interaction_deadline TIMESTAMPTZ,
    recommended_actions TEXT[],
    assessed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_risk_player_date
    ON player_risk_assessments (player_id, assessed_at DESC);

CREATE INDEX IF NOT EXISTS idx_risk_requires_interaction
    ON player_risk_assessments (requires_interaction)
    WHERE requires_interaction = TRUE;

-- UKGC requires 3-year retention; this index helps with archival queries
CREATE INDEX IF NOT EXISTS idx_risk_assessed_at
    ON player_risk_assessments (assessed_at);

COMMENT ON TABLE player_risk_assessments IS
    'Audit trail for player risk assessments. Retain minimum 3 years per UKGC LCCP.';
"""


# ---------------------------------------------------------------------------
# CLI entry point for manual evaluation
# ---------------------------------------------------------------------------

async def main():
    import argparse
    import os

    parser = argparse.ArgumentParser(description="Evaluate player risk score")
    parser.add_argument("player_id", help="Player ID to evaluate")
    parser.add_argument("--db-url", default=os.getenv(
        "DATABASE_URL", "postgresql://rg_service:rg_service@localhost:5432/responsible_gaming"
    ))
    parser.add_argument("--init-schema", action="store_true", help="Create DB tables")
    args = parser.parse_args()

    pool = await asyncpg.create_pool(args.db_url, min_size=2, max_size=5)

    if args.init_schema:
        async with pool.acquire() as conn:
            await conn.execute(SCHEMA_SQL)
        print("Schema initialized.")

    scorer = PlayerRiskScorer(db_pool=pool, config=RiskConfig())
    result = await scorer.evaluate_player(args.player_id)

    print(f"\n{'='*60}")
    print(f"Player: {result.player_id}")
    print(f"Overall Score: {result.overall_score}")
    print(f"Overall Risk: {result.overall_risk.name}")
    print(f"Requires Interaction: {result.requires_interaction}")
    if result.interaction_deadline:
        print(f"Interaction Deadline: {result.interaction_deadline.isoformat()}")
    print(f"\nIndicators:")
    for ind in result.indicators:
        print(f"  {ind.name}: {ind.score:.3f} ({ind.risk_level.name})")
        for k, v in ind.details.items():
            print(f"    {k}: {v}")
    print(f"\nRecommended Actions:")
    for action in result.recommended_actions:
        print(f"  - {action}")
    print(f"{'='*60}")

    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())

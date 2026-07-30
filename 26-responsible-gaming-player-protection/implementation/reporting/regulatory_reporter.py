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
Regulatory Reporting for Responsible Gambling Metrics
Chapter 10 - Responsible Gaming and Player Protection

Generates compliance reports for UKGC and MGA in their required formats,
covering self-exclusion statistics, intervention effectiveness, limit usage,
reality check engagement, and complaint handling.

Compliance References:
- UKGC: Annual Assurance Statement data requirements
- UKGC: Key event reporting (self-exclusions, complaints)
- UKGC LCCP 3.4.3: Evaluation of customer interaction effectiveness
- MGA: Annual compliance report requirements
- MGA Directive 2 of 2018: Player protection reporting obligations

Reports generated:
1. Self-Exclusion Summary (monthly/quarterly/annual)
2. Customer Interaction Report (UKGC LCCP 3.4.1 compliance)
3. Limit Usage Report (deposit, loss, wager, session)
4. Reality Check Engagement Report
5. Complaint & Dispute Report
6. Problem Gambling Referral Report

Usage:
    reporter = RegulatoryReporter(db_pool)
    ukgc_report = await reporter.generate_ukgc_annual_report(2025)
    mga_report = await reporter.generate_mga_quarterly_report(2025, 1)
"""

import csv
import io
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, date, timezone
from typing import Optional

import asyncpg  # ty:ignore[unresolved-import]

logger = logging.getLogger(__name__)


@dataclass
class ReportPeriod:
    start_date: date
    end_date: date
    period_type: str  # "monthly", "quarterly", "annual"


@dataclass
class SelfExclusionStats:
    total_exclusions: int = 0
    new_exclusions: int = 0
    active_exclusions: int = 0
    expired_exclusions: int = 0
    reinstated_accounts: int = 0
    avg_exclusion_months: float = 0.0
    by_duration: dict = field(default_factory=dict)  # {6: count, 12: count, 60: count}
    by_type: dict = field(default_factory=dict)       # {self: count, operator: count}
    national_scheme_registrations: int = 0


@dataclass
class InteractionStats:
    """UKGC LCCP 3.4.1 - Customer interaction report."""
    total_interactions: int = 0
    by_risk_level: dict = field(default_factory=dict)
    by_intervention_type: dict = field(default_factory=dict)
    # Effectiveness metrics (LCCP 3.4.3)
    acknowledged_pct: float = 0.0
    positive_outcome_pct: float = 0.0
    escalated_pct: float = 0.0
    self_excluded_after_interaction: int = 0
    limit_set_after_interaction: int = 0
    avg_response_time_hours: float = 0.0
    mandatory_interactions_completed: int = 0
    mandatory_interactions_overdue: int = 0


@dataclass
class LimitUsageStats:
    total_players_with_limits: int = 0
    by_limit_type: dict = field(default_factory=dict)
    limit_decreases: int = 0
    limit_increases: int = 0
    limit_removals: int = 0
    avg_deposit_limit: float = 0.0
    players_hitting_limits_pct: float = 0.0


@dataclass
class RealityCheckStats:
    total_checks_delivered: int = 0
    total_acknowledged: int = 0
    acknowledgment_rate: float = 0.0
    avg_display_duration_seconds: float = 0.0
    actions_after_check: dict = field(default_factory=dict)
    players_who_logged_out_pct: float = 0.0
    players_who_set_limit_pct: float = 0.0


# ---------------------------------------------------------------------------
# Regulatory Reporter
# ---------------------------------------------------------------------------

class RegulatoryReporter:
    """
    Generates regulatory compliance reports from the responsible gaming database.

    All monetary values are reported in the operator's base currency.
    Player counts are unique players (not events).
    """

    def __init__(self, db_pool: asyncpg.Pool):
        self.db = db_pool

    # -------------------------------------------------------------------
    # UKGC Reports
    # -------------------------------------------------------------------

    async def generate_ukgc_annual_report(self, year: int) -> dict:
        """
        Generate UKGC Annual Assurance Statement data.

        UKGC expects operators to provide evidence of:
        1. Customer identification and monitoring
        2. Customer interaction policies and outcomes
        3. Self-exclusion scheme compliance
        4. Reality check implementation
        5. Limit availability and usage
        """
        period = ReportPeriod(
            start_date=date(year, 1, 1),
            end_date=date(year, 12, 31),
            period_type="annual",
        )

        exclusion_stats = await self._self_exclusion_stats(period)
        interaction_stats = await self._interaction_stats(period)
        limit_stats = await self._limit_usage_stats(period)
        reality_stats = await self._reality_check_stats(period)
        complaint_stats = await self._complaint_stats(period)
        referral_stats = await self._referral_stats(period)

        report = {
            "report_type": "UKGC_Annual_Assurance",
            "reporting_period": {
                "start": period.start_date.isoformat(),
                "end": period.end_date.isoformat(),
                "type": "annual",
            },
            "generated_at": datetime.now(timezone.utc).isoformat(),

            "1_self_exclusion": {
                "total_self_exclusions": exclusion_stats.total_exclusions,
                "new_self_exclusions": exclusion_stats.new_exclusions,
                "currently_active": exclusion_stats.active_exclusions,
                "expired_and_reinstated": exclusion_stats.reinstated_accounts,
                "average_duration_months": exclusion_stats.avg_exclusion_months,
                "by_duration": exclusion_stats.by_duration,
                "gamstop_registrations": exclusion_stats.national_scheme_registrations,
            },

            "2_customer_interaction": {
                "total_interactions": interaction_stats.total_interactions,
                "by_risk_level": interaction_stats.by_risk_level,
                "by_type": interaction_stats.by_intervention_type,
                "effectiveness": {
                    "acknowledged_pct": interaction_stats.acknowledged_pct,
                    "positive_outcome_pct": interaction_stats.positive_outcome_pct,
                    "escalated_pct": interaction_stats.escalated_pct,
                    "self_excluded_after": interaction_stats.self_excluded_after_interaction,
                    "limit_set_after": interaction_stats.limit_set_after_interaction,
                },
                "mandatory_interactions": {
                    "completed": interaction_stats.mandatory_interactions_completed,
                    "overdue": interaction_stats.mandatory_interactions_overdue,
                    "avg_response_hours": interaction_stats.avg_response_time_hours,
                },
            },

            "3_limits": {
                "players_with_active_limits": limit_stats.total_players_with_limits,
                "by_type": limit_stats.by_limit_type,
                "limit_changes": {
                    "decreases": limit_stats.limit_decreases,
                    "increases": limit_stats.limit_increases,
                    "removals": limit_stats.limit_removals,
                },
                "avg_deposit_limit": limit_stats.avg_deposit_limit,
                "players_reaching_limits_pct": limit_stats.players_hitting_limits_pct,
            },

            "4_reality_checks": {
                "total_delivered": reality_stats.total_checks_delivered,
                "acknowledgment_rate": reality_stats.acknowledgment_rate,
                "avg_display_seconds": reality_stats.avg_display_duration_seconds,
                "player_actions": reality_stats.actions_after_check,
                "logged_out_pct": reality_stats.players_who_logged_out_pct,
                "set_limit_pct": reality_stats.players_who_set_limit_pct,
            },

            "5_complaints": complaint_stats,
            "6_referrals": referral_stats,
        }

        # Persist report
        await self._save_report("ukgc_annual", year, report)  # ty:ignore[invalid-argument-type]

        return report

    async def generate_mga_quarterly_report(
        self, year: int, quarter: int
    ) -> dict:
        """
        Generate MGA quarterly compliance report.

        MGA requires quarterly reporting on:
        - Player protection measures effectiveness
        - Self-exclusion activity
        - Limit usage statistics
        - Complaint handling
        """
        quarter_start_month = (quarter - 1) * 3 + 1
        quarter_end_month = quarter * 3
        period = ReportPeriod(
            start_date=date(year, quarter_start_month, 1),
            end_date=date(
                year if quarter_end_month <= 12 else year + 1,
                quarter_end_month if quarter_end_month <= 12 else quarter_end_month - 12,
                28,  # Safe end-of-month
            ),
            period_type="quarterly",
        )

        exclusion_stats = await self._self_exclusion_stats(period)
        interaction_stats = await self._interaction_stats(period)
        limit_stats = await self._limit_usage_stats(period)

        report = {
            "report_type": "MGA_Quarterly_Compliance",
            "reporting_period": {
                "year": year,
                "quarter": quarter,
                "start": period.start_date.isoformat(),
                "end": period.end_date.isoformat(),
            },
            "generated_at": datetime.now(timezone.utc).isoformat(),

            "player_protection": {
                "total_active_players": await self._active_player_count(period),
                "players_with_deposit_limits": limit_stats.by_limit_type.get("deposit", 0),
                "players_with_loss_limits": limit_stats.by_limit_type.get("loss", 0),
                "self_exclusions": {
                    "new": exclusion_stats.new_exclusions,
                    "active": exclusion_stats.active_exclusions,
                },
                "interventions": {
                    "total": interaction_stats.total_interactions,
                    "by_risk_level": interaction_stats.by_risk_level,
                },
            },
        }

        await self._save_report("mga_quarterly", f"{year}_Q{quarter}", report)
        return report

    # -------------------------------------------------------------------
    # CSV Export (for regulatory submissions)
    # -------------------------------------------------------------------

    async def export_self_exclusions_csv(self, period: ReportPeriod) -> str:
        """Export self-exclusion register as CSV for regulatory submission."""
        async with self.db.acquire() as conn:
            rows = await conn.fetch("""
                SELECT
                    exclusion_id,
                    player_id,
                    exclusion_type,
                    status,
                    started_at,
                    expires_at,
                    duration_months,
                    reason,
                    national_scheme,
                    national_scheme_ref,
                    balance_at_exclusion,
                    balance_withdrawn
                FROM player_exclusions
                WHERE started_at >= $1 AND started_at <= $2
                ORDER BY started_at
            """, period.start_date, period.end_date)

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "Exclusion ID", "Player ID (Hashed)", "Type", "Status",
            "Start Date", "End Date", "Duration (Months)", "Reason",
            "National Scheme", "Scheme Ref", "Balance at Exclusion",
            "Balance Withdrawn",
        ])

        for row in rows:
            # Hash player ID for external reporting (GDPR)
            import hashlib
            hashed_id = hashlib.sha256(row["player_id"].encode()).hexdigest()[:16]
            writer.writerow([
                row["exclusion_id"],
                hashed_id,
                row["exclusion_type"],
                row["status"],
                row["started_at"].strftime("%Y-%m-%d"),
                row["expires_at"].strftime("%Y-%m-%d"),
                row["duration_months"],
                row["reason"],
                row["national_scheme"] or "",
                row["national_scheme_ref"] or "",
                f"{row['balance_at_exclusion']:.2f}",
                "Yes" if row["balance_withdrawn"] else "No",
            ])

        return output.getvalue()

    async def export_interactions_csv(self, period: ReportPeriod) -> str:
        """Export customer interactions as CSV."""
        async with self.db.acquire() as conn:
            rows = await conn.fetch("""
                SELECT
                    intervention_id, player_id, intervention_type,
                    risk_level, trigger_reason, status,
                    delivered_at, acknowledged_at, completed_at,
                    outcome, player_response
                FROM rg_interventions
                WHERE created_at >= $1 AND created_at <= $2
                ORDER BY created_at
            """, period.start_date, period.end_date)

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "ID", "Player (Hashed)", "Type", "Risk Level", "Trigger",
            "Status", "Delivered", "Acknowledged", "Completed",
            "Outcome", "Player Response",
        ])

        for row in rows:
            import hashlib
            hashed_id = hashlib.sha256(row["player_id"].encode()).hexdigest()[:16]
            writer.writerow([
                row["intervention_id"],
                hashed_id,
                row["intervention_type"],
                row["risk_level"],
                row["trigger_reason"],
                row["status"],
                row["delivered_at"].strftime("%Y-%m-%d %H:%M") if row["delivered_at"] else "",
                row["acknowledged_at"].strftime("%Y-%m-%d %H:%M") if row["acknowledged_at"] else "",
                row["completed_at"].strftime("%Y-%m-%d %H:%M") if row["completed_at"] else "",
                row["outcome"] or "",
                row["player_response"] or "",
            ])

        return output.getvalue()

    # -------------------------------------------------------------------
    # Data Aggregation Queries
    # -------------------------------------------------------------------

    async def _self_exclusion_stats(self, period: ReportPeriod) -> SelfExclusionStats:
        stats = SelfExclusionStats()

        async with self.db.acquire() as conn:
            # Total and new exclusions
            row = await conn.fetchrow("""
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE started_at >= $1 AND started_at <= $2) AS new_count,
                    COUNT(*) FILTER (WHERE status = 'active') AS active,
                    COUNT(*) FILTER (WHERE status = 'expired') AS expired,
                    COUNT(*) FILTER (WHERE reinstatement_effective_at IS NOT NULL
                                     AND reinstatement_effective_at >= $1) AS reinstated,
                    COALESCE(AVG(duration_months), 0) AS avg_duration,
                    COUNT(*) FILTER (WHERE national_scheme IS NOT NULL
                                     AND started_at >= $1) AS national_registrations
                FROM player_exclusions
                WHERE started_at <= $2
            """, period.start_date, period.end_date)

            stats.total_exclusions = row["total"]
            stats.new_exclusions = row["new_count"]
            stats.active_exclusions = row["active"]
            stats.expired_exclusions = row["expired"]
            stats.reinstated_accounts = row["reinstated"]
            stats.avg_exclusion_months = float(row["avg_duration"])
            stats.national_scheme_registrations = row["national_registrations"]

            # By duration
            duration_rows = await conn.fetch("""
                SELECT duration_months, COUNT(*) AS cnt
                FROM player_exclusions
                WHERE started_at >= $1 AND started_at <= $2
                GROUP BY duration_months
            """, period.start_date, period.end_date)
            stats.by_duration = {r["duration_months"]: r["cnt"] for r in duration_rows}

            # By type
            type_rows = await conn.fetch("""
                SELECT exclusion_type, COUNT(*) AS cnt
                FROM player_exclusions
                WHERE started_at >= $1 AND started_at <= $2
                GROUP BY exclusion_type
            """, period.start_date, period.end_date)
            stats.by_type = {r["exclusion_type"]: r["cnt"] for r in type_rows}

        return stats

    async def _interaction_stats(self, period: ReportPeriod) -> InteractionStats:
        stats = InteractionStats()

        async with self.db.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE acknowledged_at IS NOT NULL) AS acknowledged,
                    COUNT(*) FILTER (WHERE outcome = 'positive_change') AS positive,
                    COUNT(*) FILTER (WHERE status = 'escalated') AS escalated,
                    COUNT(*) FILTER (WHERE outcome = 'self_excluded') AS self_excluded,
                    COUNT(*) FILTER (WHERE outcome LIKE '%%limit_set%%') AS limit_set,
                    AVG(EXTRACT(EPOCH FROM (COALESCE(acknowledged_at, NOW()) - delivered_at)) / 3600)
                        FILTER (WHERE delivered_at IS NOT NULL) AS avg_response_hours,
                    COUNT(*) FILTER (WHERE intervention_type = 'mandatory_interaction'
                                     AND status = 'completed') AS mandatory_completed,
                    COUNT(*) FILTER (WHERE intervention_type = 'mandatory_interaction'
                                     AND status NOT IN ('completed', 'expired')
                                     AND interaction_deadline < NOW()) AS mandatory_overdue
                FROM rg_interventions
                WHERE created_at >= $1 AND created_at <= $2
            """, period.start_date, period.end_date)

            stats.total_interactions = row["total"]
            if stats.total_interactions > 0:
                stats.acknowledged_pct = round(row["acknowledged"] / stats.total_interactions * 100, 1)
                stats.positive_outcome_pct = round(row["positive"] / stats.total_interactions * 100, 1)
                stats.escalated_pct = round(row["escalated"] / stats.total_interactions * 100, 1)
            stats.self_excluded_after_interaction = row["self_excluded"]
            stats.limit_set_after_interaction = row["limit_set"]
            stats.avg_response_time_hours = round(float(row["avg_response_hours"] or 0), 1)
            stats.mandatory_interactions_completed = row["mandatory_completed"]
            stats.mandatory_interactions_overdue = row["mandatory_overdue"]

            # By risk level
            level_rows = await conn.fetch("""
                SELECT risk_level, COUNT(*) AS cnt
                FROM rg_interventions
                WHERE created_at >= $1 AND created_at <= $2
                GROUP BY risk_level
            """, period.start_date, period.end_date)
            stats.by_risk_level = {r["risk_level"]: r["cnt"] for r in level_rows}

            # By intervention type
            type_rows = await conn.fetch("""
                SELECT intervention_type, COUNT(*) AS cnt
                FROM rg_interventions
                WHERE created_at >= $1 AND created_at <= $2
                GROUP BY intervention_type
            """, period.start_date, period.end_date)
            stats.by_intervention_type = {r["intervention_type"]: r["cnt"] for r in type_rows}

        return stats

    async def _limit_usage_stats(self, period: ReportPeriod) -> LimitUsageStats:
        stats = LimitUsageStats()

        async with self.db.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT COUNT(DISTINCT player_id) AS total_with_limits
                FROM player_limits
                WHERE effective_from <= $2
            """, period.end_date)
            stats.total_players_with_limits = row["total_with_limits"]

            type_rows = await conn.fetch("""
                SELECT limit_type, COUNT(DISTINCT player_id) AS cnt
                FROM player_limits
                WHERE effective_from <= $2
                GROUP BY limit_type
            """, period.end_date)
            stats.by_limit_type = {r["limit_type"]: r["cnt"] for r in type_rows}

            changes = await conn.fetchrow("""
                SELECT
                    COUNT(*) FILTER (WHERE action = 'limit_set' AND new_value < old_value) AS decreases,
                    COUNT(*) FILTER (WHERE action LIKE '%%increase%%') AS increases,
                    COUNT(*) FILTER (WHERE action LIKE '%%removal%%') AS removals
                FROM limit_audit_log
                WHERE created_at >= $1 AND created_at <= $2
            """, period.start_date, period.end_date)
            stats.limit_decreases = changes["decreases"]
            stats.limit_increases = changes["increases"]
            stats.limit_removals = changes["removals"]

            avg_row = await conn.fetchrow("""
                SELECT COALESCE(AVG(amount), 0) AS avg_deposit_limit
                FROM player_limits
                WHERE limit_type = 'deposit' AND effective_from <= $1
            """, period.end_date)
            stats.avg_deposit_limit = float(avg_row["avg_deposit_limit"])

        return stats

    async def _reality_check_stats(self, period: ReportPeriod) -> RealityCheckStats:
        stats = RealityCheckStats()

        async with self.db.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT
                    COUNT(*) AS total_delivered,
                    COUNT(*) FILTER (WHERE acknowledged_at IS NOT NULL) AS acknowledged,
                    COALESCE(AVG(display_duration_seconds)
                        FILTER (WHERE display_duration_seconds IS NOT NULL), 0) AS avg_display
                FROM reality_check_log
                WHERE created_at >= $1 AND created_at <= $2 AND delivered = TRUE
            """, period.start_date, period.end_date)

            stats.total_checks_delivered = row["total_delivered"]
            stats.total_acknowledged = row["acknowledged"]
            if stats.total_checks_delivered > 0:
                stats.acknowledgment_rate = round(
                    row["acknowledged"] / stats.total_checks_delivered * 100, 1
                )
            stats.avg_display_duration_seconds = round(float(row["avg_display"]), 1)

            action_rows = await conn.fetch("""
                SELECT action_taken, COUNT(*) AS cnt
                FROM reality_check_log
                WHERE created_at >= $1 AND created_at <= $2
                  AND action_taken IS NOT NULL
                GROUP BY action_taken
            """, period.start_date, period.end_date)
            stats.actions_after_check = {r["action_taken"]: r["cnt"] for r in action_rows}

        return stats

    async def _complaint_stats(self, period: ReportPeriod) -> dict:
        async with self.db.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE category = 'responsible_gambling') AS rg_related,
                    COUNT(*) FILTER (WHERE status = 'resolved') AS resolved,
                    COUNT(*) FILTER (WHERE escalated_to_adr = TRUE) AS escalated_adr
                FROM player_complaints
                WHERE created_at >= $1 AND created_at <= $2
            """, period.start_date, period.end_date)
        return {
            "total_complaints": row["total"] if row else 0,
            "rg_related": row["rg_related"] if row else 0,
            "resolved": row["resolved"] if row else 0,
            "escalated_to_adr": row["escalated_adr"] if row else 0,
        }

    async def _referral_stats(self, period: ReportPeriod) -> dict:
        async with self.db.acquire() as conn:
            rows = await conn.fetch("""
                SELECT referral_organization, COUNT(*) AS cnt
                FROM problem_gambling_referrals
                WHERE created_at >= $1 AND created_at <= $2
                GROUP BY referral_organization
            """, period.start_date, period.end_date)
        return {
            "total_referrals": sum(r["cnt"] for r in rows),
            "by_organization": {r["referral_organization"]: r["cnt"] for r in rows},
        }

    async def _active_player_count(self, period: ReportPeriod) -> int:
        async with self.db.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT COUNT(DISTINCT player_id) AS cnt
                FROM player_sessions
                WHERE started_at >= $1 AND started_at <= $2
            """, period.start_date, period.end_date)
        return row["cnt"] if row else 0

    async def _save_report(self, report_type: str, period_ref: str, data: dict) -> None:
        try:
            async with self.db.acquire() as conn:
                await conn.execute("""
                    INSERT INTO regulatory_reports
                        (report_type, period_ref, report_data, generated_at)
                    VALUES ($1, $2, $3::jsonb, NOW())
                """, report_type, str(period_ref), json.dumps(data, default=str))
        except Exception:
            logger.exception("Failed to save report: %s %s", report_type, period_ref)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS regulatory_reports (
    report_id       BIGSERIAL PRIMARY KEY,
    report_type     VARCHAR(50) NOT NULL,
    period_ref      VARCHAR(20) NOT NULL,
    report_data     JSONB NOT NULL,
    generated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS player_complaints (
    complaint_id    BIGSERIAL PRIMARY KEY,
    player_id       VARCHAR(64) NOT NULL,
    category        VARCHAR(50),
    status          VARCHAR(20) DEFAULT 'open',
    escalated_to_adr BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS problem_gambling_referrals (
    referral_id             BIGSERIAL PRIMARY KEY,
    player_id               VARCHAR(64) NOT NULL,
    referral_organization   VARCHAR(50) NOT NULL,
    referral_type           VARCHAR(30),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reports_type_period
    ON regulatory_reports (report_type, period_ref);
"""

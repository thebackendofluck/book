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
Comprehensive Limits Service
Chapter 10 - Responsible Gaming and Player Protection

Manages deposit, loss, wager, and session time limits with cooling-off period
enforcement. Supports per-player configurable limits with regulatory minimums.

Compliance References:
- UKGC LCCP Code 3.3.1: Financial limits must be available to all players
- MGA Player Protection Directive: Mandatory deposit limits for all accounts
- UKGC: Cooling-off period before limit increases take effect (minimum 24h)
- MGA Directive 2 of 2018: Limits decrease must take immediate effect
- Swedish Gambling Authority: Mandatory deposit limits, 72h increase delay

Key Design Decisions:
- Limit DECREASES take effect immediately (regulatory requirement)
- Limit INCREASES require a cooling-off period (default 24h, configurable per jurisdiction)
- All limit operations are audit-logged for UKGC/MGA compliance
- Redis used for real-time limit tracking; PostgreSQL for persistence
- Panic button integration: session time limit of 0 forces immediate logout

Usage:
    service = LimitsService(db_pool, redis_client)
    await service.set_limit(player_id, LimitType.DEPOSIT, LimitPeriod.DAILY, 100.00)
    can_deposit, remaining = await service.check_deposit(player_id, 50.00)
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional

import asyncpg  # ty:ignore[unresolved-import]
import redis.asyncio as aioredis

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums & Configuration
# ---------------------------------------------------------------------------

class LimitType(str, Enum):
    DEPOSIT = "deposit"
    LOSS = "loss"
    WAGER = "wager"
    SESSION_TIME = "session_time"  # in minutes


class LimitPeriod(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class CoolOffReason(str, Enum):
    PLAYER_REQUEST = "player_request"
    OPERATOR_ACTION = "operator_action"
    REGULATORY = "regulatory"


@dataclass
class JurisdictionConfig:
    """Per-jurisdiction limit rules."""
    # Cooling-off period before limit INCREASE takes effect
    increase_cooloff_hours: int = 24
    # Whether deposit limits are mandatory for all players
    mandatory_deposit_limit: bool = False
    # Default deposit limit if mandatory (e.g., Sweden: SEK 5000/week)
    default_deposit_limit: Optional[Decimal] = None
    default_deposit_period: LimitPeriod = LimitPeriod.WEEKLY
    # Maximum session time before forced reality check (minutes)
    max_session_before_reality_check: int = 60


# Pre-configured jurisdiction settings
JURISDICTION_CONFIGS = {
    "UKGC": JurisdictionConfig(
        increase_cooloff_hours=24,
        mandatory_deposit_limit=False,
        max_session_before_reality_check=60,
    ),
    "MGA": JurisdictionConfig(
        increase_cooloff_hours=24,
        mandatory_deposit_limit=True,
        default_deposit_limit=Decimal("500.00"),
        default_deposit_period=LimitPeriod.MONTHLY,
    ),
    "SGA": JurisdictionConfig(  # Swedish Gambling Authority
        increase_cooloff_hours=72,
        mandatory_deposit_limit=True,
        default_deposit_limit=Decimal("5000.00"),
        default_deposit_period=LimitPeriod.WEEKLY,
    ),
}


@dataclass
class LimitRecord:
    player_id: str
    limit_type: LimitType
    period: LimitPeriod
    amount: Decimal          # For session_time, this is minutes
    effective_from: datetime
    created_at: datetime
    pending_increase: Optional[Decimal] = None
    pending_effective: Optional[datetime] = None


@dataclass
class LimitCheckResult:
    allowed: bool
    current_usage: Decimal
    limit_amount: Decimal
    remaining: Decimal
    period: LimitPeriod
    resets_at: datetime


# ---------------------------------------------------------------------------
# Redis key helpers
# ---------------------------------------------------------------------------

def _usage_key(player_id: str, limit_type: LimitType, period: LimitPeriod) -> str:
    return f"rg:limits:{player_id}:{limit_type.value}:{period.value}"


def _session_key(player_id: str) -> str:
    return f"rg:session:{player_id}"


def _period_ttl(period: LimitPeriod) -> int:
    """TTL in seconds for Redis usage counters."""
    return {
        LimitPeriod.DAILY: 86400,
        LimitPeriod.WEEKLY: 604800,
        LimitPeriod.MONTHLY: 2678400,  # 31 days
    }[period]


# ---------------------------------------------------------------------------
# Limits Service
# ---------------------------------------------------------------------------

class LimitsService:
    """
    Manages all player limits with regulatory compliance.

    Architecture:
        - PostgreSQL: Limit definitions, audit log, pending increases
        - Redis: Real-time usage counters (deposit totals, loss totals, session timers)
        - Limit decreases are immediate (player protection)
        - Limit increases go through cooling-off period
        - Cool-off periods block all gambling activity for the player
    """

    def __init__(
        self,
        db_pool: asyncpg.Pool,
        redis: aioredis.Redis,
        jurisdiction: str = "UKGC",
    ):
        self.db = db_pool
        self.redis = redis
        self.jurisdiction = jurisdiction
        self.config = JURISDICTION_CONFIGS.get(jurisdiction, JurisdictionConfig())

    # -------------------------------------------------------------------
    # Set / Update Limits
    # -------------------------------------------------------------------

    async def set_limit(
        self,
        player_id: str,
        limit_type: LimitType,
        period: LimitPeriod,
        amount: Decimal,
        reason: str = "player_request",
    ) -> dict:
        """
        Set or update a player's limit.

        Rules:
        - Decrease: takes effect IMMEDIATELY
        - Increase: subject to cooling-off period (24-72h depending on jurisdiction)
        - Removal: treated as increase to infinity, requires cooling-off

        Returns dict with status and effective_from timestamp.
        """
        amount = Decimal(str(amount))
        current = await self._get_current_limit(player_id, limit_type, period)

        if current and amount > current.amount:
            # INCREASE: apply cooling-off period
            effective_from = datetime.now(timezone.utc) + timedelta(
                hours=self.config.increase_cooloff_hours
            )
            await self._save_pending_increase(
                player_id, limit_type, period, amount, effective_from
            )
            await self._audit_log(
                player_id, "limit_increase_pending", limit_type, period,
                current.amount, amount, effective_from, reason,
            )
            logger.info(
                "Limit increase pending for %s: %s %s %s -> %s (effective %s)",
                player_id, limit_type.value, period.value, current.amount, amount,
                effective_from.isoformat(),
            )
            return {
                "status": "pending",
                "current_amount": float(current.amount),
                "pending_amount": float(amount),
                "effective_from": effective_from.isoformat(),
                "cooloff_hours": self.config.increase_cooloff_hours,
            }
        else:
            # DECREASE or NEW: takes effect immediately
            effective_from = datetime.now(timezone.utc)
            await self._save_limit(player_id, limit_type, period, amount, effective_from)
            await self._audit_log(
                player_id, "limit_set", limit_type, period,
                current.amount if current else None, amount, effective_from, reason,
            )
            return {
                "status": "active",
                "amount": float(amount),
                "effective_from": effective_from.isoformat(),
            }

    async def remove_limit(
        self, player_id: str, limit_type: LimitType, period: LimitPeriod
    ) -> dict:
        """
        Remove a limit. Treated as an increase (to infinity), so cooling-off applies.
        MGA mandates deposit limits cannot be fully removed if mandatory.
        """
        if (
            self.config.mandatory_deposit_limit
            and limit_type == LimitType.DEPOSIT
        ):
            return {
                "status": "rejected",
                "reason": f"Deposit limits are mandatory under {self.jurisdiction} regulation",
            }

        effective_from = datetime.now(timezone.utc) + timedelta(
            hours=self.config.increase_cooloff_hours
        )
        await self._save_pending_removal(player_id, limit_type, period, effective_from)
        await self._audit_log(
            player_id, "limit_removal_pending", limit_type, period,
            None, None, effective_from, "player_request",
        )
        return {
            "status": "pending_removal",
            "effective_from": effective_from.isoformat(),
        }

    # -------------------------------------------------------------------
    # Check Limits (called before transactions)
    # -------------------------------------------------------------------

    async def check_deposit(
        self, player_id: str, amount: Decimal
    ) -> LimitCheckResult:
        """
        Check if a deposit of the given amount is allowed under all active
        deposit limits (daily, weekly, monthly). Returns the MOST RESTRICTIVE result.
        """
        return await self._check_limit(player_id, LimitType.DEPOSIT, amount)

    async def check_loss(self, player_id: str, amount: Decimal) -> LimitCheckResult:
        """Check if accumulated losses are within limits."""
        return await self._check_limit(player_id, LimitType.LOSS, amount)

    async def check_wager(self, player_id: str, amount: Decimal) -> LimitCheckResult:
        """Check if a wager is allowed under wager limits."""
        return await self._check_limit(player_id, LimitType.WAGER, amount)

    async def check_session_time(self, player_id: str) -> LimitCheckResult:
        """Check if player has exceeded session time limit."""
        session_data = await self.redis.hgetall(_session_key(player_id))  # ty:ignore[invalid-await]
        if not session_data:
            return LimitCheckResult(
                allowed=True, current_usage=Decimal("0"),
                limit_amount=Decimal("0"), remaining=Decimal("999"),
                period=LimitPeriod.DAILY,
                resets_at=datetime.now(timezone.utc) + timedelta(hours=24),
            )

        started_str = session_data.get(b"started_at", b"").decode()
        if not started_str:
            return LimitCheckResult(
                allowed=True, current_usage=Decimal("0"),
                limit_amount=Decimal("0"), remaining=Decimal("999"),
                period=LimitPeriod.DAILY,
                resets_at=datetime.now(timezone.utc) + timedelta(hours=24),
            )

        started_at = datetime.fromisoformat(started_str)
        elapsed_min = Decimal(
            str(round((datetime.now(timezone.utc) - started_at).total_seconds() / 60, 1))
        )

        limit = await self._get_current_limit(player_id, LimitType.SESSION_TIME, LimitPeriod.DAILY)
        if not limit:
            limit_amount = Decimal(str(self.config.max_session_before_reality_check))
        else:
            limit_amount = limit.amount

        remaining = limit_amount - elapsed_min
        return LimitCheckResult(
            allowed=remaining > 0,
            current_usage=elapsed_min,
            limit_amount=limit_amount,
            remaining=max(Decimal("0"), remaining),
            period=LimitPeriod.DAILY,
            resets_at=started_at + timedelta(minutes=float(limit_amount)),
        )

    async def _check_limit(
        self, player_id: str, limit_type: LimitType, amount: Decimal
    ) -> LimitCheckResult:
        """Check amount against all periods for a given limit type."""
        most_restrictive = None

        for period in LimitPeriod:
            limit = await self._get_current_limit(player_id, limit_type, period)
            if not limit:
                continue

            key = _usage_key(player_id, limit_type, period)
            current_raw = await self.redis.get(key)
            current_usage = Decimal(current_raw.decode()) if current_raw else Decimal("0")
            remaining = limit.amount - current_usage
            allowed = (current_usage + Decimal(str(amount))) <= limit.amount

            result = LimitCheckResult(
                allowed=allowed,
                current_usage=current_usage,
                limit_amount=limit.amount,
                remaining=max(Decimal("0"), remaining),
                period=period,
                resets_at=self._next_period_reset(period),
            )

            if most_restrictive is None or result.remaining < most_restrictive.remaining:
                most_restrictive = result

        if most_restrictive is None:
            return LimitCheckResult(
                allowed=True, current_usage=Decimal("0"),
                limit_amount=Decimal("0"), remaining=Decimal("999999"),
                period=LimitPeriod.DAILY,
                resets_at=self._next_period_reset(LimitPeriod.DAILY),
            )

        return most_restrictive

    # -------------------------------------------------------------------
    # Record Usage (called after successful transactions)
    # -------------------------------------------------------------------

    async def record_deposit(self, player_id: str, amount: Decimal) -> None:
        """Increment deposit usage counters across all periods."""
        await self._increment_usage(player_id, LimitType.DEPOSIT, amount)

    async def record_loss(self, player_id: str, amount: Decimal) -> None:
        """Increment loss usage counters."""
        await self._increment_usage(player_id, LimitType.LOSS, amount)

    async def record_wager(self, player_id: str, amount: Decimal) -> None:
        """Increment wager usage counters."""
        await self._increment_usage(player_id, LimitType.WAGER, amount)

    async def start_session(self, player_id: str) -> None:
        """Record session start time for session time limit tracking."""
        key = _session_key(player_id)
        await self.redis.hset(key, mapping={
            "started_at": datetime.now(timezone.utc).isoformat(),
            "player_id": player_id,
        })  # ty:ignore[invalid-await]
        await self.redis.expire(key, 86400)

    async def end_session(self, player_id: str) -> None:
        """Clear session timer on logout or session end."""
        await self.redis.delete(_session_key(player_id))

    async def _increment_usage(
        self, player_id: str, limit_type: LimitType, amount: Decimal
    ) -> None:
        pipe = self.redis.pipeline()
        for period in LimitPeriod:
            key = _usage_key(player_id, limit_type, period)
            pipe.incrbyfloat(key, float(amount))
            pipe.expire(key, _period_ttl(period))
        await pipe.execute()

    # -------------------------------------------------------------------
    # Cool-Off Periods
    # -------------------------------------------------------------------

    async def activate_cool_off(
        self,
        player_id: str,
        duration_hours: int,
        reason: CoolOffReason = CoolOffReason.PLAYER_REQUEST,
    ) -> dict:
        """
        Activate a cooling-off period. During cool-off, the player cannot:
        - Place bets or wagers
        - Make deposits
        - Access games

        The cool-off takes effect IMMEDIATELY (UKGC/MGA requirement).
        """
        expires_at = datetime.now(timezone.utc) + timedelta(hours=duration_hours)
        cool_off_key = f"rg:cooloff:{player_id}"

        await self.redis.set(
            cool_off_key,
            json.dumps({
                "player_id": player_id,
                "reason": reason.value,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "expires_at": expires_at.isoformat(),
                "duration_hours": duration_hours,
            }),
            ex=duration_hours * 3600,
        )

        # Force end any active session
        await self.end_session(player_id)

        await self._audit_log(
            player_id, "cool_off_activated", None, None,
            None, Decimal(str(duration_hours)), expires_at, reason.value,
        )

        logger.info("Cool-off activated for %s: %dh until %s", player_id, duration_hours, expires_at)
        return {
            "status": "active",
            "expires_at": expires_at.isoformat(),
            "duration_hours": duration_hours,
        }

    async def is_cooled_off(self, player_id: str) -> tuple[bool, Optional[datetime]]:
        """Check if player is in cooling-off period."""
        cool_off_key = f"rg:cooloff:{player_id}"
        data = await self.redis.get(cool_off_key)
        if not data:
            return False, None
        parsed = json.loads(data)
        expires_at = datetime.fromisoformat(parsed["expires_at"])
        return True, expires_at

    # -------------------------------------------------------------------
    # Pending Limit Processor (run as scheduled job)
    # -------------------------------------------------------------------

    async def process_pending_increases(self) -> int:
        """
        Apply pending limit increases whose cooling-off has expired.
        Run this as a periodic task (e.g., every 5 minutes via cron or scheduler).
        """
        async with self.db.acquire() as conn:
            pending = await conn.fetch("""
                SELECT player_id, limit_type, period, pending_amount, pending_effective
                FROM player_limits
                WHERE pending_amount IS NOT NULL
                  AND pending_effective <= NOW()
            """)

            count = 0
            for row in pending:
                await conn.execute("""
                    UPDATE player_limits
                    SET amount = pending_amount,
                        pending_amount = NULL,
                        pending_effective = NULL,
                        effective_from = NOW(),
                        updated_at = NOW()
                    WHERE player_id = $1 AND limit_type = $2 AND period = $3
                """, row["player_id"], row["limit_type"], row["period"])
                count += 1
                logger.info(
                    "Pending limit increase applied: %s %s %s -> %s",
                    row["player_id"], row["limit_type"], row["period"],
                    row["pending_amount"],
                )

        return count

    # -------------------------------------------------------------------
    # Get Player Limits (for dashboard display)
    # -------------------------------------------------------------------

    async def get_player_limits(self, player_id: str) -> list[dict]:
        """Return all active limits and usage for a player."""
        async with self.db.acquire() as conn:
            limits = await conn.fetch("""
                SELECT limit_type, period, amount, pending_amount,
                       pending_effective, effective_from
                FROM player_limits
                WHERE player_id = $1
                ORDER BY limit_type, period
            """, player_id)

        result = []
        for lim in limits:
            lt = LimitType(lim["limit_type"])
            period = LimitPeriod(lim["period"])
            key = _usage_key(player_id, lt, period)
            usage_raw = await self.redis.get(key)
            current_usage = float(usage_raw.decode()) if usage_raw else 0.0

            result.append({
                "limit_type": lt.value,
                "period": period.value,
                "amount": float(lim["amount"]),
                "current_usage": current_usage,
                "remaining": max(0, float(lim["amount"]) - current_usage),
                "utilization_pct": round(
                    current_usage / float(lim["amount"]) * 100, 1
                ) if float(lim["amount"]) > 0 else 0,
                "pending_increase": float(lim["pending_amount"]) if lim["pending_amount"] else None,
                "pending_effective": lim["pending_effective"].isoformat() if lim["pending_effective"] else None,
            })

        return result

    # -------------------------------------------------------------------
    # Internal Helpers
    # -------------------------------------------------------------------

    async def _get_current_limit(
        self, player_id: str, limit_type: LimitType, period: LimitPeriod
    ) -> Optional[LimitRecord]:
        async with self.db.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT amount, effective_from, created_at, pending_amount, pending_effective
                FROM player_limits
                WHERE player_id = $1 AND limit_type = $2 AND period = $3
                  AND effective_from <= NOW()
            """, player_id, limit_type.value, period.value)

        if not row:
            return None
        return LimitRecord(
            player_id=player_id,
            limit_type=limit_type,
            period=period,
            amount=Decimal(str(row["amount"])),
            effective_from=row["effective_from"],
            created_at=row["created_at"],
            pending_increase=Decimal(str(row["pending_amount"])) if row["pending_amount"] else None,
            pending_effective=row["pending_effective"],
        )

    async def _save_limit(
        self, player_id: str, limit_type: LimitType, period: LimitPeriod,
        amount: Decimal, effective_from: datetime
    ) -> None:
        async with self.db.acquire() as conn:
            await conn.execute("""
                INSERT INTO player_limits (player_id, limit_type, period, amount, effective_from)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (player_id, limit_type, period)
                DO UPDATE SET amount = $4, effective_from = $5,
                             pending_amount = NULL, pending_effective = NULL,
                             updated_at = NOW()
            """, player_id, limit_type.value, period.value, amount, effective_from)

    async def _save_pending_increase(
        self, player_id: str, limit_type: LimitType, period: LimitPeriod,
        amount: Decimal, effective_from: datetime
    ) -> None:
        async with self.db.acquire() as conn:
            await conn.execute("""
                UPDATE player_limits
                SET pending_amount = $4, pending_effective = $5, updated_at = NOW()
                WHERE player_id = $1 AND limit_type = $2 AND period = $3
            """, player_id, limit_type.value, period.value, amount, effective_from)

    async def _save_pending_removal(
        self, player_id: str, limit_type: LimitType, period: LimitPeriod,
        effective_from: datetime
    ) -> None:
        async with self.db.acquire() as conn:
            await conn.execute("""
                UPDATE player_limits
                SET pending_amount = 0, pending_effective = $4, updated_at = NOW()
                WHERE player_id = $1 AND limit_type = $2 AND period = $3
            """, player_id, limit_type.value, period.value, effective_from)

    async def _audit_log(
        self, player_id: str, action: str,
        limit_type: Optional[LimitType], period: Optional[LimitPeriod],
        old_value: Optional[Decimal], new_value: Optional[Decimal],
        effective_from: Optional[datetime], reason: str,
    ) -> None:
        async with self.db.acquire() as conn:
            await conn.execute("""
                INSERT INTO limit_audit_log
                    (player_id, action, limit_type, period, old_value, new_value,
                     effective_from, reason, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())
            """,
                player_id, action,
                limit_type.value if limit_type else None,
                period.value if period else None,
                old_value, new_value, effective_from, reason,
            )

    @staticmethod
    def _next_period_reset(period: LimitPeriod) -> datetime:
        now = datetime.now(timezone.utc)
        if period == LimitPeriod.DAILY:
            return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == LimitPeriod.WEEKLY:
            days_until_monday = (7 - now.weekday()) % 7 or 7
            return (now + timedelta(days=days_until_monday)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        else:  # MONTHLY
            if now.month == 12:
                return now.replace(year=now.year + 1, month=1, day=1,
                                   hour=0, minute=0, second=0, microsecond=0)
            return now.replace(month=now.month + 1, day=1,
                               hour=0, minute=0, second=0, microsecond=0)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS player_limits (
    player_id       VARCHAR(64) NOT NULL,
    limit_type      VARCHAR(20) NOT NULL,
    period          VARCHAR(10) NOT NULL,
    amount          NUMERIC(14,2) NOT NULL,
    effective_from  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    pending_amount  NUMERIC(14,2),
    pending_effective TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (player_id, limit_type, period)
);

CREATE TABLE IF NOT EXISTS limit_audit_log (
    log_id          BIGSERIAL PRIMARY KEY,
    player_id       VARCHAR(64) NOT NULL,
    action          VARCHAR(40) NOT NULL,
    limit_type      VARCHAR(20),
    period          VARCHAR(10),
    old_value       NUMERIC(14,2),
    new_value       NUMERIC(14,2),
    effective_from  TIMESTAMPTZ,
    reason          VARCHAR(100),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_limit_audit_player
    ON limit_audit_log (player_id, created_at DESC);

COMMENT ON TABLE player_limits IS
    'Player gambling limits. Decreases immediate, increases require cooling-off.';
COMMENT ON TABLE limit_audit_log IS
    'Audit trail for all limit changes. Retain 3+ years per UKGC/MGA.';
"""

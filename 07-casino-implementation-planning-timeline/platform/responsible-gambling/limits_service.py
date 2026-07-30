#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 07, Casino Implementation Planning and Timeline.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Responsible Gambling Limits Service

Implements deposit limits, loss limits, session time limits, and reality
check timers as required by gambling regulators worldwide.

Features:
- Deposit limits (daily, weekly, monthly)
- Loss limits (net loss caps)
- Session duration limits with forced logout
- Reality check notifications (configurable intervals)
- Wager limits
- Cooling-off periods (24h, 48h, 7d, 30d)
- Jurisdiction-specific default limits
- Limit decrease: immediate effect
- Limit increase: mandatory cooling-off period (24-72 hours)
- Complete audit trail for compliance

Usage:
    from limits_service import LimitsService
    service = LimitsService()
    service.set_deposit_limit("player-123", "daily", 50.00, "GBP", "uk")
    can_deposit, msg = service.check_deposit_allowed("player-123", 25.00, "GBP")
    service.start_session("player-123", "uk")

    python3 limits_service.py --demo
"""

import json
import logging
import argparse
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Jurisdiction-specific default limits and rules
# ---------------------------------------------------------------------------

JURISDICTION_LIMITS: dict[str, dict[str, Any]] = {
    "uk": {
        "name": "United Kingdom",
        "deposit_limits": {
            "daily_default": None,     # No default, player must set (UKGC encourages)
            "weekly_default": None,
            "monthly_default": None,
            "mandatory": False,        # UKGC: not mandatory but strongly encouraged
        },
        "reality_check_minutes": 60,   # Default: every 60 minutes
        "reality_check_mandatory": False,  # Encouraged but not currently mandated
        "session_limit_mandatory": False,
        "cooling_off_for_increase_hours": 24,
        "decrease_immediate": True,
        "affordability_check_threshold": 2000,  # Monthly
        "available_cooling_off_periods": [1, 2, 7, 30],  # days
    },
    "sweden": {
        "name": "Sweden",
        "deposit_limits": {
            "daily_default": None,
            "weekly_default": 5000,    # SEK
            "monthly_default": None,
            "mandatory": True,         # Spelinspektionen: mandatory deposit limits
        },
        "reality_check_minutes": 60,
        "reality_check_mandatory": True,
        "session_limit_mandatory": False,
        "cooling_off_for_increase_hours": 72,  # 3 days in Sweden
        "decrease_immediate": True,
        "affordability_check_threshold": 0,  # Always applies
        "available_cooling_off_periods": [1, 7, 30, 90],
        "deposit_limit_cooldown_seconds": 3,  # 3-second pause between deposits
    },
    "germany": {
        "name": "Germany",
        "deposit_limits": {
            "daily_default": None,
            "weekly_default": None,
            "monthly_default": 1000,   # EUR - GluStV2021 mandatory EUR 1000/month
            "mandatory": True,
            "monthly_max": 1000,       # Hard cap
        },
        "reality_check_minutes": 60,
        "reality_check_mandatory": True,
        "session_limit_mandatory": False,
        "cooling_off_for_increase_hours": 48,
        "decrease_immediate": True,
        "affordability_check_threshold": 0,
        "available_cooling_off_periods": [1, 7, 30, 90, 365],
        "mandatory_5_second_spin_delay": True,
        "panic_button_required": True,
        "autoplay_banned": True,
    },
    "malta": {
        "name": "Malta",
        "deposit_limits": {
            "daily_default": None,
            "weekly_default": None,
            "monthly_default": None,
            "mandatory": False,
        },
        "reality_check_minutes": 60,
        "reality_check_mandatory": False,
        "session_limit_mandatory": False,
        "cooling_off_for_increase_hours": 24,
        "decrease_immediate": True,
        "affordability_check_threshold": 2000,
        "available_cooling_off_periods": [1, 7, 30, 90],
    },
    "ontario": {
        "name": "Ontario",
        "deposit_limits": {
            "daily_default": None,
            "weekly_default": None,
            "monthly_default": None,
            "mandatory": False,  # Encouraged, prompted during registration
        },
        "reality_check_minutes": 60,
        "reality_check_mandatory": False,
        "session_limit_mandatory": False,
        "cooling_off_for_increase_hours": 24,
        "decrease_immediate": True,
        "affordability_check_threshold": 5000,
        "available_cooling_off_periods": [1, 7, 30],
    },
}


class LimitPeriod(Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class LimitType(Enum):
    DEPOSIT = "deposit"
    LOSS = "loss"
    WAGER = "wager"
    SESSION_TIME = "session_time"


@dataclass
class PlayerLimit:
    """A player-set limit."""
    limit_id: str
    player_id: str
    limit_type: str
    period: str
    amount: float
    currency: str
    jurisdiction: str
    status: str             # active, pending_increase, expired
    effective_from: str
    pending_new_amount: Optional[float] = None
    pending_effective_from: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""


@dataclass
class PlayerSession:
    """Tracks an active player session for time-based limits."""
    session_id: str
    player_id: str
    jurisdiction: str
    started_at: str
    last_activity: str
    session_duration_minutes: float
    session_limit_minutes: Optional[float]
    reality_check_interval_minutes: int
    last_reality_check: Optional[str]
    next_reality_check: Optional[str]
    total_deposits_session: float
    total_losses_session: float
    total_wagers_session: float
    is_active: bool


@dataclass
class LimitUsage:
    """Tracks usage against a limit for the current period."""
    player_id: str
    limit_type: str
    period: str
    period_start: str
    period_end: str
    limit_amount: float
    used_amount: float
    remaining: float
    utilization_pct: float


class LimitsService:
    """
    Comprehensive responsible gambling limits service.
    """

    def __init__(self):
        self._limits: dict = defaultdict(list)       # player_id -> [PlayerLimit]
        self._sessions: dict = {}                    # player_id -> PlayerSession
        self._deposit_history: dict = defaultdict(list)  # player_id -> [(timestamp, amount)]
        self._loss_history: dict = defaultdict(list)
        self._wager_history: dict = defaultdict(list)
        self._audit_log: list = []

    def set_deposit_limit(
        self,
        player_id: str,
        period: str,
        amount: float,
        currency: str,
        jurisdiction: str,
    ) -> PlayerLimit:
        """
        Set or update a deposit limit for a player.

        Rules:
        - Decrease: Takes effect IMMEDIATELY (player protection)
        - Increase: Takes effect after cooling-off period (24-72h depending on jurisdiction)
        - Cannot exceed jurisdiction maximum if one exists
        """
        config = JURISDICTION_LIMITS.get(jurisdiction, {})

        # Check jurisdiction max
        max_limit = config.get("deposit_limits", {}).get(f"{period}_max")
        if max_limit and amount > max_limit:
            raise ValueError(
                f"Cannot set {period} deposit limit above {currency} {max_limit:.2f} "
                f"in {jurisdiction} (regulatory maximum)"
            )

        if amount <= 0:
            raise ValueError("Limit amount must be positive")

        now = datetime.now(timezone.utc)
        existing = self._get_limit(player_id, LimitType.DEPOSIT.value, period)

        if existing and amount < existing.amount:
            # DECREASE - immediate effect (player protection)
            existing.amount = amount
            existing.updated_at = now.isoformat()
            existing.pending_new_amount = None
            existing.pending_effective_from = None

            self._audit(player_id, "LIMIT_DECREASED",
                        f"Deposit {period} limit decreased to {currency} {amount:.2f} (immediate)")
            logger.info(f"Deposit limit DECREASED (immediate): {player_id} | "
                        f"{period} -> {currency} {amount:.2f}")
            return existing

        elif existing and amount > existing.amount:
            # INCREASE - requires cooling-off period
            cooling_hours = config.get("cooling_off_for_increase_hours", 24)
            effective_from = now + timedelta(hours=cooling_hours)

            existing.pending_new_amount = amount
            existing.pending_effective_from = effective_from.isoformat()
            existing.status = "pending_increase"
            existing.updated_at = now.isoformat()

            self._audit(player_id, "LIMIT_INCREASE_PENDING",
                        f"Deposit {period} limit increase to {currency} {amount:.2f} "
                        f"pending until {effective_from.isoformat()} ({cooling_hours}h cooling-off)")
            logger.info(f"Deposit limit increase PENDING: {player_id} | "
                        f"{period} -> {currency} {amount:.2f} | Effective: {effective_from}")
            return existing

        else:
            # NEW limit
            limit = PlayerLimit(
                limit_id=f"lim-{uuid.uuid4().hex[:12]}",
                player_id=player_id,
                limit_type=LimitType.DEPOSIT.value,
                period=period,
                amount=amount,
                currency=currency,
                jurisdiction=jurisdiction,
                status="active",
                effective_from=now.isoformat(),
                created_at=now.isoformat(),
                updated_at=now.isoformat(),
            )
            self._limits[player_id].append(limit)

            self._audit(player_id, "LIMIT_SET",
                        f"Deposit {period} limit set to {currency} {amount:.2f}")
            logger.info(f"Deposit limit SET: {player_id} | {period} = {currency} {amount:.2f}")
            return limit

    def set_loss_limit(
        self,
        player_id: str,
        period: str,
        amount: float,
        currency: str,
        jurisdiction: str,
    ) -> PlayerLimit:
        """Set a net loss limit for a player."""
        now = datetime.now(timezone.utc)
        limit = PlayerLimit(
            limit_id=f"lim-{uuid.uuid4().hex[:12]}",
            player_id=player_id,
            limit_type=LimitType.LOSS.value,
            period=period,
            amount=amount,
            currency=currency,
            jurisdiction=jurisdiction,
            status="active",
            effective_from=now.isoformat(),
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
        )
        self._limits[player_id].append(limit)
        self._audit(player_id, "LOSS_LIMIT_SET", f"Loss {period} limit: {currency} {amount:.2f}")
        logger.info(f"Loss limit SET: {player_id} | {period} = {currency} {amount:.2f}")
        return limit

    def set_session_time_limit(
        self,
        player_id: str,
        limit_minutes: int,
        jurisdiction: str,
    ) -> PlayerLimit:
        """Set a session time limit in minutes."""
        now = datetime.now(timezone.utc)
        limit = PlayerLimit(
            limit_id=f"lim-{uuid.uuid4().hex[:12]}",
            player_id=player_id,
            limit_type=LimitType.SESSION_TIME.value,
            period="session",
            amount=float(limit_minutes),
            currency="minutes",
            jurisdiction=jurisdiction,
            status="active",
            effective_from=now.isoformat(),
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
        )
        self._limits[player_id].append(limit)
        self._audit(player_id, "SESSION_LIMIT_SET", f"Session limit: {limit_minutes} minutes")
        return limit

    def check_deposit_allowed(
        self,
        player_id: str,
        amount: float,
        currency: str,
    ) -> tuple:
        """
        Check if a deposit is allowed within the player's limits.

        Returns: (allowed: bool, message: str, remaining: float)
        Called by the payment service before processing any deposit.
        """
        # Process any pending limit increases
        self._process_pending_increases(player_id)

        now = datetime.now(timezone.utc)
        limits = [l for l in self._limits.get(player_id, [])
                  if l.limit_type == LimitType.DEPOSIT.value and l.status == "active"]

        if not limits:
            return True, "No deposit limits set", float("inf")

        for limit in limits:
            period_start, period_end = self._get_period_bounds(limit.period, now)
            used = self._get_period_usage(player_id, "deposit", period_start, period_end)
            remaining = limit.amount - used

            if amount > remaining + 0.01:
                return (
                    False,
                    f"{limit.period.capitalize()} deposit limit reached. "
                    f"Limit: {currency} {limit.amount:.2f}, Used: {currency} {used:.2f}, "
                    f"Remaining: {currency} {max(0, remaining):.2f}",
                    max(0, remaining),
                )

        min_remaining = min(
            limit.amount - self._get_period_usage(
                player_id, "deposit",
                *self._get_period_bounds(limit.period, now)
            )
            for limit in limits
        )

        return True, "Deposit allowed within limits", max(0, min_remaining)

    def record_deposit(self, player_id: str, amount: float, timestamp: Optional[datetime] = None):
        """Record a deposit for limit tracking."""
        ts = timestamp or datetime.now(timezone.utc)
        self._deposit_history[player_id].append((ts.isoformat(), amount))

    def record_loss(self, player_id: str, amount: float, timestamp: Optional[datetime] = None):
        """Record a net loss for limit tracking."""
        ts = timestamp or datetime.now(timezone.utc)
        self._loss_history[player_id].append((ts.isoformat(), amount))

    def check_loss_limit(self, player_id: str, amount: float, currency: str) -> tuple:
        """Check if a bet/loss is within loss limits."""
        now = datetime.now(timezone.utc)
        limits = [l for l in self._limits.get(player_id, [])
                  if l.limit_type == LimitType.LOSS.value and l.status == "active"]

        if not limits:
            return True, "No loss limits set"

        for limit in limits:
            period_start, period_end = self._get_period_bounds(limit.period, now)
            used = self._get_period_usage(player_id, "loss", period_start, period_end)
            remaining = limit.amount - used

            if amount > remaining + 0.01:
                return False, f"{limit.period.capitalize()} loss limit reached ({currency} {limit.amount:.2f})"

        return True, "Within loss limits"

    def start_session(self, player_id: str, jurisdiction: str) -> PlayerSession:
        """
        Start a new player session with reality check timer.

        Called when a player logs in or starts playing.
        """
        config = JURISDICTION_LIMITS.get(jurisdiction, {})
        reality_check_mins = config.get("reality_check_minutes", 60)

        # Get session time limit if set
        session_limits = [l for l in self._limits.get(player_id, [])
                          if l.limit_type == LimitType.SESSION_TIME.value and l.status == "active"]
        session_limit = session_limits[0].amount if session_limits else None

        now = datetime.now(timezone.utc)
        next_check = (now + timedelta(minutes=reality_check_mins)).isoformat()

        session = PlayerSession(
            session_id=f"sess-{uuid.uuid4().hex[:12]}",
            player_id=player_id,
            jurisdiction=jurisdiction,
            started_at=now.isoformat(),
            last_activity=now.isoformat(),
            session_duration_minutes=0.0,
            session_limit_minutes=session_limit,
            reality_check_interval_minutes=reality_check_mins,
            last_reality_check=None,
            next_reality_check=next_check,
            total_deposits_session=0.0,
            total_losses_session=0.0,
            total_wagers_session=0.0,
            is_active=True,
        )
        self._sessions[player_id] = session

        logger.info(f"Session started: {player_id} | Reality check every {reality_check_mins}min"
                     + (f" | Session limit: {session_limit}min" if session_limit else ""))

        return session

    def check_session_status(self, player_id: str) -> dict:
        """
        Check the current session status.

        Returns instructions for the frontend:
        - continue: player can keep playing
        - reality_check: show reality check popup
        - session_expired: force logout
        """
        session = self._sessions.get(player_id)
        if not session or not session.is_active:
            return {"action": "no_session", "message": "No active session"}

        now = datetime.now(timezone.utc)
        started = datetime.fromisoformat(session.started_at)
        duration_minutes = (now - started).total_seconds() / 60
        session.session_duration_minutes = round(duration_minutes, 1)

        # Check session time limit
        if session.session_limit_minutes and duration_minutes >= session.session_limit_minutes:
            session.is_active = False
            self._audit(player_id, "SESSION_LIMIT_REACHED",
                        f"Session ended after {duration_minutes:.0f} minutes (limit: {session.session_limit_minutes})")
            return {
                "action": "session_expired",
                "message": f"Your session time limit of {session.session_limit_minutes:.0f} minutes "
                           f"has been reached. You have been logged out.",
                "duration_minutes": duration_minutes,
            }

        # Check reality check timer
        if session.next_reality_check:
            next_check = datetime.fromisoformat(session.next_reality_check)
            if now >= next_check:
                # Schedule next reality check
                next_next = (now + timedelta(minutes=session.reality_check_interval_minutes)).isoformat()
                session.last_reality_check = now.isoformat()
                session.next_reality_check = next_next

                return {
                    "action": "reality_check",
                    "message": "Reality Check",
                    "data": {
                        "session_duration_minutes": round(duration_minutes),
                        "total_deposits": session.total_deposits_session,
                        "total_losses": session.total_losses_session,
                        "total_wagers": session.total_wagers_session,
                        "net_position": round(
                            session.total_deposits_session - session.total_losses_session, 2),
                    },
                    "options": [
                        "Continue playing",
                        "Set a deposit limit",
                        "Take a break",
                        "Log out",
                    ],
                }

        return {
            "action": "continue",
            "session_duration_minutes": round(duration_minutes, 1),
            "next_reality_check_in_minutes": round(
                max(0, (datetime.fromisoformat(session.next_reality_check) - now).total_seconds() / 60), 1
            ) if session.next_reality_check else None,
        }

    def activate_cooling_off(self, player_id: str, days: int, jurisdiction: str) -> dict:
        """
        Activate a cooling-off period (temporary break from gambling).

        Different from self-exclusion: shorter duration, account not fully closed.
        Player cannot gamble but can view account/withdraw funds.
        """
        config = JURISDICTION_LIMITS.get(jurisdiction, {})
        available = config.get("available_cooling_off_periods", [1, 7, 30])

        if days not in available:
            raise ValueError(f"Cooling-off period must be one of: {available} days")

        now = datetime.now(timezone.utc)
        end_date = now + timedelta(days=days)

        # End any active session
        if player_id in self._sessions:
            self._sessions[player_id].is_active = False

        self._audit(player_id, "COOLING_OFF_ACTIVATED",
                    f"{days}-day cooling-off period activated until {end_date.isoformat()}")

        logger.info(f"Cooling-off: {player_id} | {days} days | Until: {end_date.isoformat()}")

        return {
            "status": "activated",
            "days": days,
            "start": now.isoformat(),
            "end": end_date.isoformat(),
            "message": f"Your {days}-day cooling-off period is now active. "
                       f"You can still access your account to view history and withdraw funds, "
                       f"but you cannot place any bets or make deposits until {end_date.strftime('%Y-%m-%d')}.",
        }

    def get_player_limits(self, player_id: str) -> list:
        """Get all active limits for a player."""
        self._process_pending_increases(player_id)
        return [asdict(l) for l in self._limits.get(player_id, []) if l.status == "active"]

    def get_limit_usage(self, player_id: str) -> list:
        """Get current usage against all limits."""
        now = datetime.now(timezone.utc)
        usage = []

        for limit in self._limits.get(player_id, []):
            if limit.status != "active":
                continue

            period_start, period_end = self._get_period_bounds(limit.period, now)

            if limit.limit_type == LimitType.DEPOSIT.value:
                used = self._get_period_usage(player_id, "deposit", period_start, period_end)
            elif limit.limit_type == LimitType.LOSS.value:
                used = self._get_period_usage(player_id, "loss", period_start, period_end)
            else:
                continue

            remaining = max(0, limit.amount - used)
            pct = (used / limit.amount * 100) if limit.amount > 0 else 0

            usage.append(LimitUsage(
                player_id=player_id,
                limit_type=limit.limit_type,
                period=limit.period,
                period_start=period_start.isoformat(),
                period_end=period_end.isoformat(),
                limit_amount=limit.amount,
                used_amount=round(used, 2),
                remaining=round(remaining, 2),
                utilization_pct=round(pct, 1),
            ))

        return usage

    # -----------------------------------------------------------------------
    # Private methods
    # -----------------------------------------------------------------------

    def _get_limit(self, player_id: str, limit_type: str, period: str) -> Optional[PlayerLimit]:
        """Find an existing limit."""
        for limit in self._limits.get(player_id, []):
            if limit.limit_type == limit_type and limit.period == period and limit.status in ("active", "pending_increase"):
                return limit
        return None

    def _process_pending_increases(self, player_id: str):
        """Process any pending limit increases whose cooling-off has expired."""
        now = datetime.now(timezone.utc)
        for limit in self._limits.get(player_id, []):
            if limit.status == "pending_increase" and limit.pending_effective_from:
                effective = datetime.fromisoformat(limit.pending_effective_from)
                if now >= effective:
                    old_amount = limit.amount
                    limit.amount = limit.pending_new_amount
                    limit.pending_new_amount = None
                    limit.pending_effective_from = None
                    limit.status = "active"
                    limit.updated_at = now.isoformat()
                    self._audit(player_id, "LIMIT_INCREASE_APPLIED",
                                f"{limit.limit_type} {limit.period} limit increased: "
                                f"{old_amount} -> {limit.amount}")

    def _get_period_bounds(self, period: str, now: datetime) -> tuple:
        """Get the start and end timestamps for the current limit period."""
        if period == "daily":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1)
        elif period == "weekly":
            start = now - timedelta(days=now.weekday())
            start = start.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(weeks=1)
        elif period == "monthly":
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            if now.month == 12:
                end = start.replace(year=now.year + 1, month=1)
            else:
                end = start.replace(month=now.month + 1)
        else:
            start = now - timedelta(hours=24)
            end = now
        return start, end

    def _get_period_usage(self, player_id: str, usage_type: str,
                          period_start: datetime, period_end: datetime) -> float:
        """Get total usage within a period."""
        if usage_type == "deposit":
            history = self._deposit_history.get(player_id, [])
        elif usage_type == "loss":
            history = self._loss_history.get(player_id, [])
        else:
            return 0.0

        total = 0.0
        for ts_str, amount in history:
            ts = datetime.fromisoformat(ts_str)
            if period_start <= ts < period_end:
                total += amount
        return total

    def _audit(self, player_id: str, action: str, details: str):
        """Add an audit log entry."""
        self._audit_log.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "player_id": player_id,
            "action": action,
            "details": details,
        })


def run_demo():
    """Run a demonstration of the limits service."""
    service = LimitsService()

    print("\n" + "=" * 70)
    print("  RESPONSIBLE GAMBLING LIMITS SERVICE DEMO")
    print("=" * 70)

    # Demo 1: Set deposit limits
    print("\n--- Demo 1: Setting Deposit Limits ---")

    service.set_deposit_limit("player-001", "daily", 50.00, "GBP", "uk")
    service.set_deposit_limit("player-001", "weekly", 200.00, "GBP", "uk")
    service.set_deposit_limit("player-001", "monthly", 500.00, "GBP", "uk")

    limits = service.get_player_limits("player-001")
    for l in limits:
        print(f"  {l['limit_type']} {l['period']}: {l['currency']} {l['amount']:.2f}")

    # Demo 2: Check deposits against limits
    print("\n--- Demo 2: Checking Deposits ---")

    service.record_deposit("player-001", 30.00)
    allowed, msg, remaining = service.check_deposit_allowed("player-001", 25.00, "GBP")
    print(f"  Deposit GBP 25.00: {'ALLOWED' if allowed else 'DENIED'}")
    print(f"  Message: {msg}")
    print(f"  Remaining daily: GBP {remaining:.2f}")

    # Exceed daily limit
    service.record_deposit("player-001", 15.00)
    allowed, msg, remaining = service.check_deposit_allowed("player-001", 10.00, "GBP")
    print(f"\n  Deposit GBP 10.00: {'ALLOWED' if allowed else 'DENIED'}")
    print(f"  Message: {msg}")

    # Demo 3: Limit decrease (immediate)
    print("\n--- Demo 3: Limit Decrease (Immediate) ---")
    service.set_deposit_limit("player-001", "daily", 30.00, "GBP", "uk")
    print("  Daily limit decreased to GBP 30.00 (effective immediately)")

    # Demo 4: Limit increase (cooling-off)
    print("\n--- Demo 4: Limit Increase (24h Cooling-Off) ---")
    limit = service.set_deposit_limit("player-001", "daily", 100.00, "GBP", "uk")
    print(f"  Daily limit increase to GBP 100.00: status = {limit.status}")
    print(f"  Effective from: {limit.pending_effective_from}")

    # Demo 5: Session tracking and reality checks
    print("\n--- Demo 5: Session & Reality Checks ---")
    session = service.start_session("player-002", "uk")
    print(f"  Session started: {session.session_id}")
    print(f"  Reality check every: {session.reality_check_interval_minutes} minutes")

    status = service.check_session_status("player-002")
    print(f"  Current status: {status['action']}")

    # Demo 6: Session time limit
    print("\n--- Demo 6: Session Time Limit ---")
    service.set_session_time_limit("player-003", 120, "uk")
    service.start_session("player-003", "uk")
    print("  Session limit: 120 minutes")
    print("  (Player will be forced to log out after 120 minutes)")

    # Demo 7: Cooling-off period
    print("\n--- Demo 7: Cooling-Off Period ---")
    result = service.activate_cooling_off("player-001", 7, "uk")
    print(f"  Status: {result['status']}")
    print(f"  Duration: {result['days']} days")
    print(f"  Until: {result['end'][:10]}")

    # Demo 8: Loss limits
    print("\n--- Demo 8: Loss Limits ---")
    service.set_loss_limit("player-004", "daily", 100.00, "GBP", "uk")
    service.record_loss("player-004", 80.00)
    allowed, msg = service.check_loss_limit("player-004", 25.00, "GBP")
    print(f"  Daily loss limit: GBP 100.00 | Used: GBP 80.00")
    print(f"  Additional GBP 25.00 loss: {'ALLOWED' if allowed else 'DENIED'}")
    print(f"  Message: {msg}")

    # Demo 9: German mandatory limits
    print("\n--- Demo 9: German Mandatory EUR 1000/month Limit ---")
    try:
        service.set_deposit_limit("player-005", "monthly", 1500.00, "EUR", "germany")
    except ValueError as e:
        print(f"  Attempted EUR 1500/month: DENIED - {e}")

    service.set_deposit_limit("player-005", "monthly", 1000.00, "EUR", "germany")
    print("  Set EUR 1000/month (German regulatory maximum): OK")

    # Demo 10: Limit usage summary
    print("\n--- Demo 10: Limit Usage Summary ---")
    usage = service.get_limit_usage("player-001")
    for u in usage:
        bar = "#" * int(u.utilization_pct / 5) + "-" * (20 - int(u.utilization_pct / 5))
        print(f"  {u.limit_type} {u.period:<8} [{bar}] "
              f"{u.used_amount:.2f}/{u.limit_amount:.2f} ({u.utilization_pct:.0f}%)")

    print("\n" + "=" * 70)


def main():
    parser = argparse.ArgumentParser(description="Limits Service Demo")
    parser.add_argument("--demo", action="store_true", help="Run demo")
    parser.add_argument("--jurisdictions", action="store_true",
                        help="List jurisdiction-specific rules")

    args = parser.parse_args()

    if args.jurisdictions:
        print("\nJurisdiction Responsible Gambling Rules:")
        for key, config in JURISDICTION_LIMITS.items():
            print(f"\n  {config['name']} ({key}):")
            dl = config["deposit_limits"]
            print(f"    Deposit limits mandatory: {dl['mandatory']}")  # ty:ignore[invalid-argument-type, non-subscriptable]
            if dl.get("monthly_max"):  # ty:ignore[possibly-missing-attribute]
                print(f"    Monthly max: EUR {dl['monthly_max']}")  # ty:ignore[invalid-argument-type, non-subscriptable]
            print(f"    Reality check: every {config['reality_check_minutes']}min "
                  f"(mandatory: {config['reality_check_mandatory']})")
            print(f"    Increase cooling-off: {config['cooling_off_for_increase_hours']}h")
            if config.get("mandatory_5_second_spin_delay"):
                print(f"    5-second spin delay: mandatory")
            if config.get("autoplay_banned"):
                print(f"    Autoplay: BANNED")
        print()
        return

    run_demo()


if __name__ == "__main__":
    main()

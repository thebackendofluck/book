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
deposit_limit_enforcer.py — MGA Player Protection Directive deposit limit enforcement.

Jurisdiction:       Malta (and all MGA-licensed operators globally)
Regulator:          Malta Gaming Authority (MGA)
Regulation refs:
  - Player Protection Directive (Directive 2 of 2018), Version 3 (January 2023)
    https://www.mga.org.mt/app/uploads/Directive-2-of-2018-Player-Protection-Directive.pdf
  - MGA Player Protection Directive 2.0 amendments (effective January 2024)
    https://www.mga.org.mt/mga-new-gaming-framework-directives-financial-transition/
    directive-2-of-2018-player-protection-directive/
  - MGA Compliance Directive (Directive 3 of 2018)
    https://www.mga.org.mt/licensee-hub/compliance/
  - AML/CFT Requirements for Gaming Operators
    https://www.mga.org.mt/licensee-hub/compliance/aml-cft/
Penalties:
  - Administrative fines up to €5,000,000 or 1% of annual turnover
  - Licence suspension or revocation
  - Mandatory public disclosure of enforcement actions
  - Personal liability for Key Function roles (Article 17 PPD)

Key requirements (PPD Articles 7, 8, 9):
  - Mandatory deposit limit prompt before the first deposit of each player
  - Cool-off periods: 24h before daily limit increase, 7 days for weekly,
    30 days for monthly
  - Operators must honour limits immediately upon player request (decrease)
  - Increases only apply after the cool-off period expires
  - Cross-brand aggregation for operators within the same group
  - Limits must be clearly displayed and easily accessible
  - Reality check reminders every 30 minutes during active sessions

Book chapter:  Chapter 26 — Responsible Gaming Systems
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

import structlog

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Cool-off periods (PPD Article 8)
# ---------------------------------------------------------------------------

COOLOFF_DAILY_HOURS: int = 24
COOLOFF_WEEKLY_HOURS: int = 7 * 24       # 7 days
COOLOFF_MONTHLY_HOURS: int = 30 * 24     # 30 days


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class LimitPeriod(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class LimitChangeType(str, Enum):
    INCREASE = "increase"
    DECREASE = "decrease"
    REMOVAL = "removal"


class DepositDecision(str, Enum):
    ALLOWED = "allowed"
    BLOCKED_LIMIT_REACHED = "blocked_limit_reached"
    BLOCKED_PENDING_SET = "blocked_pending_set"     # player never set a limit


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class DepositLimit:
    """A single player-set deposit limit."""
    limit_id: str
    player_id: str
    brand_id: str                           # for cross-brand aggregation
    period: LimitPeriod
    amount: Decimal
    currency: str
    set_at: datetime
    effective_at: datetime                  # may be in the future (increase cooloff)
    expires_at: Optional[datetime] = None  # None = no expiry
    superseded_by: Optional[str] = None    # limit_id of the replacement limit

    @property
    def is_active(self) -> bool:
        now = datetime.now(timezone.utc)
        if self.effective_at > now:
            return False
        if self.expires_at and self.expires_at <= now:
            return False
        if self.superseded_by:
            return False
        return True


@dataclass
class LimitChangeRequest:
    """Pending limit change — may be subject to a cool-off period."""
    request_id: str
    player_id: str
    period: LimitPeriod
    change_type: LimitChangeType
    current_amount: Optional[Decimal]
    requested_amount: Optional[Decimal]    # None for removal
    requested_at: datetime
    effective_at: datetime                 # when the change takes effect
    applied: bool = False
    applied_at: Optional[datetime] = None


@dataclass
class DepositCheckResult:
    """Result of evaluating a deposit request against active limits."""
    check_id: str
    player_id: str
    requested_amount: Decimal
    currency: str
    decision: DepositDecision
    applicable_limit: Optional[DepositLimit]
    current_spend: Decimal
    remaining_allowance: Decimal
    checked_at: datetime
    brand_aggregated: bool = False


@dataclass
class PlayerLimitProfile:
    """Complete limit profile for a player across all periods and brands."""
    player_id: str
    limits: list[DepositLimit] = field(default_factory=list)
    pending_changes: list[LimitChangeRequest] = field(default_factory=list)
    first_deposit_prompt_shown: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def active_limit(self, period: LimitPeriod) -> Optional[DepositLimit]:
        """Return the currently active limit for a given period."""
        return next(
            (l for l in self.limits if l.period == period and l.is_active),
            None,
        )


# ---------------------------------------------------------------------------
# Deposit limit enforcer
# ---------------------------------------------------------------------------

_COOLOFF_HOURS: dict[LimitPeriod, int] = {
    LimitPeriod.DAILY: COOLOFF_DAILY_HOURS,
    LimitPeriod.WEEKLY: COOLOFF_WEEKLY_HOURS,
    LimitPeriod.MONTHLY: COOLOFF_MONTHLY_HOURS,
}


class DepositLimitEnforcer:
    """
    Enforces MGA PPD deposit limits throughout the player lifecycle.

    Responsibilities:
      1. Prompt for limits before the first deposit
      2. Evaluate every deposit request against current limits
      3. Apply limit decreases immediately, increases after cool-off
      4. Aggregate limits across brands in the same operator group
      5. Maintain complete audit trail for MGA inspection
    """

    # ------------------------------------------------------------------
    # First-deposit prompt
    # ------------------------------------------------------------------

    def record_first_deposit_prompt(
        self, profile: PlayerLimitProfile
    ) -> PlayerLimitProfile:
        """
        Record that the mandatory deposit limit prompt was shown.
        Must be called before allowing the first deposit.
        """
        profile.first_deposit_prompt_shown = True
        log.info("mga_ppd: first-deposit limit prompt shown",
                 player_id=profile.player_id)
        return profile

    def is_first_deposit_prompt_required(
        self, profile: PlayerLimitProfile
    ) -> bool:
        """Return True if the player must see the limit prompt before depositing."""
        return not profile.first_deposit_prompt_shown

    # ------------------------------------------------------------------
    # Limit setting
    # ------------------------------------------------------------------

    def set_limit(
        self,
        profile: PlayerLimitProfile,
        period: LimitPeriod,
        amount: Decimal,
        currency: str,
        brand_id: str,
    ) -> DepositLimit:
        """
        Set a new deposit limit.  Effective immediately if this is the
        first limit set for this period, or if it is a decrease.
        """
        existing = profile.active_limit(period)
        is_decrease = existing is None or amount <= existing.amount
        now = datetime.now(timezone.utc)
        cooloff_hours = _COOLOFF_HOURS[period]

        effective_at = now if is_decrease else now + timedelta(hours=cooloff_hours)

        # Supersede existing limit
        if existing:
            existing.superseded_by = "pending"   # updated below after creation

        limit = DepositLimit(
            limit_id=f"LIM-{uuid.uuid4().hex[:10].upper()}",
            player_id=profile.player_id,
            brand_id=brand_id,
            period=period,
            amount=amount,
            currency=currency,
            set_at=now,
            effective_at=effective_at,
        )

        if existing:
            existing.superseded_by = limit.limit_id

        profile.limits.append(limit)

        log.info(
            "mga_ppd: limit set",
            player_id=profile.player_id,
            period=period.value,
            amount=str(amount),
            currency=currency,
            effective_at=effective_at.isoformat(),
            cooloff_applied=not is_decrease,
        )
        return limit

    def request_limit_increase(
        self,
        profile: PlayerLimitProfile,
        period: LimitPeriod,
        new_amount: Decimal,
        brand_id: str,
        currency: str,
    ) -> LimitChangeRequest:
        """
        Request a limit increase.  Will be applied after the cool-off period.
        The existing (lower) limit remains in force until effective_at.
        """
        now = datetime.now(timezone.utc)
        existing = profile.active_limit(period)
        cooloff = timedelta(hours=_COOLOFF_HOURS[period])

        req = LimitChangeRequest(
            request_id=f"LCHG-{uuid.uuid4().hex[:10].upper()}",
            player_id=profile.player_id,
            period=period,
            change_type=LimitChangeType.INCREASE,
            current_amount=existing.amount if existing else None,
            requested_amount=new_amount,
            requested_at=now,
            effective_at=now + cooloff,
        )
        profile.pending_changes.append(req)

        log.info(
            "mga_ppd: limit increase requested",
            player_id=profile.player_id,
            period=period.value,
            current=str(existing.amount if existing else None),
            requested=str(new_amount),
            effective_at=req.effective_at.isoformat(),
        )
        return req

    def apply_pending_changes(
        self, profile: PlayerLimitProfile, brand_id: str, currency: str
    ) -> list[LimitChangeRequest]:
        """
        Process any pending limit changes whose cool-off period has expired.
        Call this on a regular schedule (e.g., every 15 minutes).
        """
        now = datetime.now(timezone.utc)
        applied: list[LimitChangeRequest] = []

        for change in profile.pending_changes:
            if change.applied:
                continue
            if now >= change.effective_at:
                if change.requested_amount is not None:
                    self.set_limit(
                        profile, change.period,
                        change.requested_amount, currency, brand_id
                    )
                change.applied = True
                change.applied_at = now
                applied.append(change)
                log.info("mga_ppd: pending limit change applied",
                         request_id=change.request_id,
                         player_id=profile.player_id,
                         period=change.period.value,
                         new_amount=str(change.requested_amount))

        return applied

    # ------------------------------------------------------------------
    # Deposit evaluation
    # ------------------------------------------------------------------

    def check_deposit(
        self,
        profile: PlayerLimitProfile,
        requested_amount: Decimal,
        currency: str,
        current_spend_by_period: dict[LimitPeriod, Decimal],
        cross_brand_spend: Optional[dict[LimitPeriod, Decimal]] = None,
    ) -> DepositCheckResult:
        """
        Evaluate a deposit request against all active limits.

        current_spend_by_period: sum of approved deposits in current period
            for this brand only.
        cross_brand_spend: sum from other brands in the same operator group
            (used for group-level aggregation per MGA PPD).
        """
        check_id = f"DEP-{uuid.uuid4().hex[:10].upper()}"
        now = datetime.now(timezone.utc)

        # Check mandatory first-deposit prompt
        if self.is_first_deposit_prompt_required(profile):
            return DepositCheckResult(
                check_id=check_id,
                player_id=profile.player_id,
                requested_amount=requested_amount,
                currency=currency,
                decision=DepositDecision.BLOCKED_PENDING_SET,
                applicable_limit=None,
                current_spend=Decimal("0"),
                remaining_allowance=Decimal("0"),
                checked_at=now,
            )

        # Check each period from most restrictive (daily) to least (monthly)
        for period in (LimitPeriod.DAILY, LimitPeriod.WEEKLY, LimitPeriod.MONTHLY):
            limit = profile.active_limit(period)
            if not limit:
                continue

            this_brand_spend = current_spend_by_period.get(period, Decimal("0"))
            other_brand_spend = (
                cross_brand_spend.get(period, Decimal("0"))
                if cross_brand_spend else Decimal("0")
            )
            total_spend = this_brand_spend + other_brand_spend
            remaining = limit.amount - total_spend
            brand_aggregated = other_brand_spend > Decimal("0")

            if requested_amount > remaining:
                log.warning(
                    "mga_ppd: deposit blocked — limit reached",
                    player_id=profile.player_id,
                    period=period.value,
                    limit=str(limit.amount),
                    spent=str(total_spend),
                    remaining=str(remaining),
                    requested=str(requested_amount),
                    brand_aggregated=brand_aggregated,
                )
                return DepositCheckResult(
                    check_id=check_id,
                    player_id=profile.player_id,
                    requested_amount=requested_amount,
                    currency=currency,
                    decision=DepositDecision.BLOCKED_LIMIT_REACHED,
                    applicable_limit=limit,
                    current_spend=total_spend,
                    remaining_allowance=max(Decimal("0"), remaining),
                    checked_at=now,
                    brand_aggregated=brand_aggregated,
                )

        # All limits satisfied
        return DepositCheckResult(
            check_id=check_id,
            player_id=profile.player_id,
            requested_amount=requested_amount,
            currency=currency,
            decision=DepositDecision.ALLOWED,
            applicable_limit=None,
            current_spend=sum(
                current_spend_by_period.values(), Decimal("0")
            ),
            remaining_allowance=Decimal("0"),   # not meaningful when allowed
            checked_at=now,
        )


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

def _demo() -> None:
    enforcer = DepositLimitEnforcer()
    profile = PlayerLimitProfile(player_id="player-mt-7001")

    # Simulate first-deposit prompt
    print(f"Prompt required: {enforcer.is_first_deposit_prompt_required(profile)}")
    enforcer.record_first_deposit_prompt(profile)

    # Player sets a €200/day limit
    daily_limit = enforcer.set_limit(
        profile,
        period=LimitPeriod.DAILY,
        amount=Decimal("200.00"),
        currency="EUR",
        brand_id="brand-casino-a",
    )
    print(f"Daily limit set: €{daily_limit.amount} (active: {daily_limit.is_active})")

    # Check a €150 deposit
    result = enforcer.check_deposit(
        profile,
        requested_amount=Decimal("150.00"),
        currency="EUR",
        current_spend_by_period={LimitPeriod.DAILY: Decimal("100.00")},
    )
    print(f"Deposit €150: {result.decision.value} "
          f"(remaining: €{result.remaining_allowance})")


if __name__ == "__main__":
    _demo()

# Companion code for "The Backend of Luck" - Chapter 37, Marketing Technology and CRM Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
customer_profile.py -- Customer profile domain model for the iGaming CDP.

CustomerProfile represents the enriched, unified view of a player
built from CDP events, game activity, payment history, and ML predictions.

Updated by Apache Flink pipeline in real time.
Cached in Redis for sub-millisecond personalisation reads.
Persisted in PostgreSQL as the source of truth.

Chapter 37: Marketing Technology and CRM
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class CustomerProfile:
    """
    Enriched player profile for personalisation, CRM, and responsible gaming.

    Fields:
        customer_id:              Canonical player ID (after identity resolution)
        email:                    PII — encrypted at rest, used for email marketing
        phone:                    PII — encrypted at rest, used for SMS
        first_seen:               UTC datetime of first event on any channel
        last_active:              UTC datetime of most recent event

        # Financial aggregates (computed by Flink, updated on each event)
        total_deposits:           Cumulative deposit amount in base currency (EUR)
        total_withdrawals:        Cumulative withdrawal amount
        total_bets:               Total number of individual bets placed
        total_wagered:            Total amount wagered (gross, before wins)
        net_gaming_revenue:       total_wagered - total_wins (operator's GGR from this player)

        # Behavioural preferences (derived from game + session patterns)
        favourite_game_category:  'slots', 'live_casino', 'table', 'sports'
        preferred_channel:        'email', 'push', 'sms' (highest engagement channel)
        preferred_session_time:   'morning', 'afternoon', 'evening', 'night'

        # ML predictions (updated hourly by prediction pipeline)
        ltv_estimate:             Predicted 12-month lifetime value in EUR
        churn_probability:        0.0 (no risk) to 1.0 (certain to churn within 30 days)
        risk_score:               Responsible gaming risk, 0.0 to 1.0

        # Segmentation
        segments:                 ['vip', 'slots_player', 'weekend_warrior', 'high_roller', ...]

        # GDPR consent (always source of truth — never infer from activity)
        consent:                  {'analytics': True, 'marketing': False, 'personalisation': True}
    """

    customer_id: str
    email: Optional[str]
    phone: Optional[str]
    first_seen: datetime
    last_active: datetime

    # Financial aggregates (updated by Flink pipeline)
    total_deposits: float
    total_withdrawals: float
    total_bets: int
    total_wagered: float
    net_gaming_revenue: float

    # Behavioural preferences
    favourite_game_category: str
    preferred_channel: str           # 'email', 'push', 'sms'
    preferred_session_time: str      # 'morning', 'afternoon', 'evening', 'night'

    # ML predictions
    ltv_estimate: float              # 12-month predicted LTV in EUR
    churn_probability: float         # 0.0–1.0
    risk_score: float                # responsible gaming risk, 0.0–1.0

    # Segmentation
    segments: list[str] = field(default_factory=list)

    # GDPR consent (always explicit — never inferred)
    consent: dict[str, bool] = field(default_factory=lambda: {
        "essential": True,
        "analytics": False,
        "marketing": False,
        "personalisation": False,
    })

    # ---------------------------------------------------------------------------
    # Responsible gaming helpers
    # ---------------------------------------------------------------------------

    @property
    def is_high_risk(self) -> bool:
        """True if risk_score exceeds the threshold for bonus suppression."""
        return self.risk_score >= 0.7

    @property
    def is_at_risk(self) -> bool:
        """True if risk_score is elevated but not yet high-risk."""
        return 0.4 <= self.risk_score < 0.7

    @property
    def should_suppress_marketing(self) -> bool:
        """
        True if the player should not receive marketing communications.
        Combines GDPR consent with responsible gaming checks.
        """
        return (
            not self.consent.get("marketing", False)
            or self.is_high_risk
        )

    # ---------------------------------------------------------------------------
    # Segment helpers
    # ---------------------------------------------------------------------------

    @property
    def is_vip(self) -> bool:
        return "vip" in self.segments

    @property
    def is_churned(self) -> bool:
        return "churned" in self.segments

    @property
    def is_high_roller(self) -> bool:
        return "high_roller" in self.segments

    # ---------------------------------------------------------------------------
    # Cache key
    # ---------------------------------------------------------------------------

    @property
    def redis_key(self) -> str:
        """Redis key for caching this profile."""
        return f"profile:{self.customer_id}"

    # ---------------------------------------------------------------------------
    # LTV buckets (for campaign targeting)
    # ---------------------------------------------------------------------------

    @property
    def ltv_tier(self) -> str:
        """Bucket LTV estimate into campaign-friendly tiers."""
        if self.ltv_estimate >= 5000:
            return "whale"
        if self.ltv_estimate >= 1000:
            return "high"
        if self.ltv_estimate >= 200:
            return "medium"
        return "low"

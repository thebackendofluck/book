#!/usr/bin/env python3
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
Multi-Touch Attribution for Casino Marketing
=============================================
Implements multiple attribution models to measure channel effectiveness
for player acquisition and reactivation campaigns.

Models:
- Last Touch: 100% credit to last touchpoint before conversion
- First Touch: 100% credit to first touchpoint
- Linear: Equal credit across all touchpoints
- Time Decay: Exponential decay favoring recent touchpoints
- Position Based (U-shaped): 40% first, 40% last, 20% distributed
- Shapley Value: Game-theoretic fair allocation (gold standard)

Casino-Specific Considerations:
- Conversion = First Deposit (FTD), not just registration
- Attribution window: typically 30 days for acquisition, 7 for reactivation
- Regulatory: UK ASA requires clear affiliate attribution for compliance
- Self-excluded players must be excluded from all attribution
"""

import math
import logging
from itertools import combinations
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional
from collections import defaultdict

logger = logging.getLogger(__name__)


@dataclass
class Touchpoint:
    """A single marketing touchpoint in a player's journey."""
    touchpoint_id: str
    channel: str           # paid_search, display, affiliate, email, social, seo, direct
    campaign_id: str
    campaign_name: str
    source: str            # google, facebook, affiliate_id, crm
    medium: str            # cpc, banner, email, push
    timestamp: datetime
    cost: float = 0.0      # Media cost for this impression/click
    player_id: Optional[str] = None
    is_click: bool = True  # Click vs. view-through


@dataclass
class Conversion:
    """A conversion event (typically First Time Deposit in casino context)."""
    conversion_id: str
    player_id: str
    conversion_type: str   # ftd, reactivation_deposit, bonus_claim
    revenue: float         # First deposit amount
    timestamp: datetime
    lifetime_value: float = 0.0  # Projected LTV at time of conversion


@dataclass
class AttributionResult:
    """Attribution credit for a single touchpoint."""
    touchpoint_id: str
    channel: str
    campaign_id: str
    credit: float          # 0.0 to 1.0
    attributed_revenue: float
    attributed_cost: float
    model: str


class AttributionWindow:
    """
    Attribution window configuration.
    Casino best practices:
    - Acquisition: 30-day click, 1-day view-through
    - Reactivation: 7-day click, 1-day view-through
    - Affiliate: as per affiliate contract (typically 30-90 days)
    """
    def __init__(self, click_window_days: int = 30,
                 view_window_days: int = 1):
        self.click_window = timedelta(days=click_window_days)
        self.view_window = timedelta(days=view_window_days)

    def is_within_window(self, touchpoint: Touchpoint,
                         conversion: Conversion) -> bool:
        delta = conversion.timestamp - touchpoint.timestamp
        if delta.total_seconds() < 0:
            return False
        if touchpoint.is_click:
            return delta <= self.click_window
        return delta <= self.view_window


class MultiTouchAttributionEngine:
    """
    Multi-touch attribution engine for casino marketing.
    """

    def __init__(self, window: Optional[AttributionWindow] = None):
        self.window = window or AttributionWindow()
        self._touchpoints: dict[str, list[Touchpoint]] = defaultdict(list)
        self._conversions: dict[str, Conversion] = {}

    def add_touchpoint(self, tp: Touchpoint):
        self._touchpoints[tp.player_id].append(tp)  # ty:ignore[invalid-argument-type]

    def add_conversion(self, conv: Conversion):
        self._conversions[conv.player_id] = conv

    def get_journey(self, player_id: str) -> list[Touchpoint]:
        """Get touchpoints within the attribution window for a player."""
        conv = self._conversions.get(player_id)
        if not conv:
            return []

        journey = [
            tp for tp in self._touchpoints.get(player_id, [])
            if self.window.is_within_window(tp, conv)
        ]
        journey.sort(key=lambda tp: tp.timestamp)
        return journey

    # ----- Attribution Models -----

    def last_touch(self, player_id: str) -> list[AttributionResult]:
        """100% credit to the last touchpoint before conversion."""
        journey = self.get_journey(player_id)
        conv = self._conversions.get(player_id)
        if not journey or not conv:
            return []

        last = journey[-1]
        return [AttributionResult(
            touchpoint_id=last.touchpoint_id,
            channel=last.channel,
            campaign_id=last.campaign_id,
            credit=1.0,
            attributed_revenue=conv.revenue,
            attributed_cost=last.cost,
            model="last_touch",
        )]

    def first_touch(self, player_id: str) -> list[AttributionResult]:
        """100% credit to the first touchpoint."""
        journey = self.get_journey(player_id)
        conv = self._conversions.get(player_id)
        if not journey or not conv:
            return []

        first = journey[0]
        return [AttributionResult(
            touchpoint_id=first.touchpoint_id,
            channel=first.channel,
            campaign_id=first.campaign_id,
            credit=1.0,
            attributed_revenue=conv.revenue,
            attributed_cost=first.cost,
            model="first_touch",
        )]

    def linear(self, player_id: str) -> list[AttributionResult]:
        """Equal credit across all touchpoints."""
        journey = self.get_journey(player_id)
        conv = self._conversions.get(player_id)
        if not journey or not conv:
            return []

        share = 1.0 / len(journey)
        return [
            AttributionResult(
                touchpoint_id=tp.touchpoint_id,
                channel=tp.channel,
                campaign_id=tp.campaign_id,
                credit=share,
                attributed_revenue=conv.revenue * share,
                attributed_cost=tp.cost * share,
                model="linear",
            )
            for tp in journey
        ]

    def time_decay(self, player_id: str,
                   half_life_days: float = 7.0) -> list[AttributionResult]:
        """
        Exponential decay: touchpoints closer to conversion get more credit.
        Half-life: time after which a touchpoint gets 50% credit.
        Casino default: 7-day half-life.
        """
        journey = self.get_journey(player_id)
        conv = self._conversions.get(player_id)
        if not journey or not conv:
            return []

        decay_rate = math.log(2) / (half_life_days * 86400)  # per second
        weights = []
        for tp in journey:
            seconds_before = (conv.timestamp - tp.timestamp).total_seconds()
            weight = math.exp(-decay_rate * seconds_before)
            weights.append(weight)

        total_weight = sum(weights)
        if total_weight == 0:
            return self.linear(player_id)

        return [
            AttributionResult(
                touchpoint_id=tp.touchpoint_id,
                channel=tp.channel,
                campaign_id=tp.campaign_id,
                credit=w / total_weight,
                attributed_revenue=conv.revenue * (w / total_weight),
                attributed_cost=tp.cost * (w / total_weight),
                model="time_decay",
            )
            for tp, w in zip(journey, weights)
        ]

    def position_based(self, player_id: str,
                       first_weight: float = 0.4,
                       last_weight: float = 0.4) -> list[AttributionResult]:
        """
        U-shaped: 40% first, 40% last, 20% distributed to middle.
        Also known as "bathtub" model.
        """
        journey = self.get_journey(player_id)
        conv = self._conversions.get(player_id)
        if not journey or not conv:
            return []

        if len(journey) == 1:
            return self.last_touch(player_id)

        middle_weight = 1.0 - first_weight - last_weight
        middle_share = middle_weight / max(1, len(journey) - 2)

        results = []
        for i, tp in enumerate(journey):
            if i == 0:
                credit = first_weight
            elif i == len(journey) - 1:
                credit = last_weight
            else:
                credit = middle_share

            results.append(AttributionResult(
                touchpoint_id=tp.touchpoint_id,
                channel=tp.channel,
                campaign_id=tp.campaign_id,
                credit=credit,
                attributed_revenue=conv.revenue * credit,
                attributed_cost=tp.cost * credit,
                model="position_based",
            ))
        return results

    def shapley_value(self, player_id: str) -> list[AttributionResult]:
        """
        Shapley value attribution: game-theoretic fair allocation.

        For each channel, calculates its marginal contribution across
        all possible coalitions. Computationally expensive for >10
        touchpoints; falls back to time_decay for long journeys.

        This is the gold standard for attribution but requires a
        conversion probability model.
        """
        journey = self.get_journey(player_id)
        conv = self._conversions.get(player_id)
        if not journey or not conv:
            return []

        # Deduplicate by channel for Shapley (coalition-based)
        channels = list({tp.channel for tp in journey})

        if len(channels) > 10:
            logger.warning(
                "Too many channels (%d) for Shapley; falling back to time_decay",
                len(channels),
            )
            return self.time_decay(player_id)

        # Build channel -> touchpoints mapping
        channel_tps: dict[str, list[Touchpoint]] = defaultdict(list)
        for tp in journey:
            channel_tps[tp.channel].append(tp)

        n = len(channels)
        shapley_values: dict[str, float] = {ch: 0.0 for ch in channels}

        # For each channel, compute marginal contribution in all coalitions
        for ch in channels:
            others = [c for c in channels if c != ch]
            for size in range(len(others) + 1):
                for coalition in combinations(others, size):
                    coalition_set = set(coalition)

                    # Conversion probability with and without this channel
                    p_with = self._coalition_conversion_prob(
                        coalition_set | {ch}, channel_tps
                    )
                    p_without = self._coalition_conversion_prob(
                        coalition_set, channel_tps
                    )

                    marginal = p_with - p_without

                    # Shapley weight: |S|!(n-|S|-1)! / n!
                    s = len(coalition_set)
                    weight = (
                        math.factorial(s) * math.factorial(n - s - 1)
                        / math.factorial(n)
                    )
                    shapley_values[ch] += weight * marginal

        # Normalize to sum to 1.0
        total = sum(shapley_values.values())
        if total <= 0:
            return self.linear(player_id)

        results = []
        for ch in channels:
            credit = shapley_values[ch] / total
            # Distribute credit among touchpoints of same channel
            tps = channel_tps[ch]
            per_tp_credit = credit / len(tps)
            for tp in tps:
                results.append(AttributionResult(
                    touchpoint_id=tp.touchpoint_id,
                    channel=tp.channel,
                    campaign_id=tp.campaign_id,
                    credit=per_tp_credit,
                    attributed_revenue=conv.revenue * per_tp_credit,
                    attributed_cost=tp.cost * per_tp_credit,
                    model="shapley",
                ))

        return results

    def _coalition_conversion_prob(
        self, channels: set, channel_tps: dict[str, list]
    ) -> float:
        """
        Estimate conversion probability for a coalition of channels.

        In production, this would use a logistic regression or gradient
        boosting model trained on historical conversion data.
        Here we use a simplified heuristic based on channel effectiveness.
        """
        if not channels:
            return 0.0

        # Simplified channel effectiveness scores (casino-tuned)
        base_probs = {
            "paid_search": 0.15,
            "display": 0.05,
            "affiliate": 0.20,
            "email": 0.12,
            "social": 0.08,
            "seo": 0.10,
            "direct": 0.25,
            "push": 0.06,
            "sms": 0.09,
        }

        # Independent probability model: P(convert) = 1 - prod(1 - p_i)
        prob_no_convert = 1.0
        for ch in channels:
            p = base_probs.get(ch, 0.05)
            # Scale by number of touchpoints (diminishing returns)
            n_tps = len(channel_tps.get(ch, []))
            adjusted_p = min(0.5, p * math.log(1 + n_tps))
            prob_no_convert *= (1 - adjusted_p)

        return 1 - prob_no_convert

    # ----- Aggregate Reporting -----

    def channel_report(self, model: str = "shapley") -> dict:
        """
        Generate channel-level attribution report across all conversions.

        Returns:
            {
                "channel_name": {
                    "attributed_conversions": float,
                    "attributed_revenue": float,
                    "total_cost": float,
                    "roas": float,   # Return on Ad Spend
                    "cpa": float,    # Cost per Acquisition
                }
            }
        """
        model_fn = {
            "last_touch": self.last_touch,
            "first_touch": self.first_touch,
            "linear": self.linear,
            "time_decay": self.time_decay,
            "position_based": self.position_based,
            "shapley": self.shapley_value,
        }.get(model, self.shapley_value)

        channel_stats: dict[str, dict] = defaultdict(
            lambda: {
                "attributed_conversions": 0.0,
                "attributed_revenue": 0.0,
                "total_cost": 0.0,
            }
        )

        for player_id in self._conversions:
            results = model_fn(player_id)
            for r in results:
                stats = channel_stats[r.channel]
                stats["attributed_conversions"] += r.credit
                stats["attributed_revenue"] += r.attributed_revenue
                stats["total_cost"] += r.attributed_cost

        # Calculate derived metrics
        for ch, stats in channel_stats.items():
            cost = stats["total_cost"]
            rev = stats["attributed_revenue"]
            convs = stats["attributed_conversions"]
            stats["roas"] = rev / cost if cost > 0 else float("inf")
            stats["cpa"] = cost / convs if convs > 0 else 0.0

        return dict(channel_stats)


# ---------------------------------------------------------------------------
# Usage Example
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    engine = MultiTouchAttributionEngine(
        window=AttributionWindow(click_window_days=30, view_window_days=1)
    )

    now = datetime.utcnow()  # ty:ignore[deprecated]

    # Player journey: sees display ad -> clicks affiliate -> searches Google -> deposits
    touchpoints = [
        Touchpoint("tp1", "display", "camp_summer", "Summer Promo", "gdn",
                    "banner", now - timedelta(days=20), cost=0.50,
                    player_id="player_1", is_click=False),
        Touchpoint("tp2", "affiliate", "aff_123", "TopCasinos Review", "topcasinos",
                    "referral", now - timedelta(days=12), cost=45.00,
                    player_id="player_1"),
        Touchpoint("tp3", "paid_search", "camp_brand", "Brand Search", "google",
                    "cpc", now - timedelta(days=2), cost=3.50,
                    player_id="player_1"),
        Touchpoint("tp4", "email", "camp_welcome", "Welcome Email", "crm",
                    "email", now - timedelta(hours=6), cost=0.02,
                    player_id="player_1"),
    ]

    for tp in touchpoints:
        engine.add_touchpoint(tp)

    engine.add_conversion(Conversion(
        conversion_id="conv_1",
        player_id="player_1",
        conversion_type="ftd",
        revenue=200.0,
        timestamp=now,
        lifetime_value=1500.0,
    ))

    # Compare all models
    models = ["last_touch", "first_touch", "linear", "time_decay",
              "position_based", "shapley"]

    for model_name in models:
        print(f"\n=== {model_name.upper()} ===")
        fn = getattr(engine, model_name)
        results = fn("player_1")
        for r in results:
            print(f"  {r.channel:15s} | credit={r.credit:.3f} | "
                  f"revenue={r.attributed_revenue:8.2f} | cost={r.attributed_cost:6.2f}")

    # Channel report
    print("\n=== CHANNEL REPORT (Shapley) ===")
    report = engine.channel_report("shapley")
    for ch, stats in report.items():
        print(f"  {ch:15s} | convs={stats['attributed_conversions']:.2f} | "
              f"rev={stats['attributed_revenue']:8.2f} | "
              f"cost={stats['total_cost']:6.2f} | "
              f"ROAS={stats['roas']:.2f}")

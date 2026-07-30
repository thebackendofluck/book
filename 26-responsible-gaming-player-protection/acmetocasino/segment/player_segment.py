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
segment.player_segment — Core Segmentation Models
==================================================

``PlayerSegment`` is the authoritative record of a player's current position
in the platform's CRM hierarchy.  It is the single source of truth for:

* **Tier** — Bronze → Silver → Gold → Platinum → VIP
* **Lifetime value** — cumulative deposits and wagered amounts
* **Risk category** — ranging from LOW_RISK to PROBLEM_GAMBLING_RISK
* **Product preferences** — which game categories the player gravitates toward
* **Activity metadata** — signup date, last active timestamp, custom tags

Design notes
------------
* All monetary values use ``Decimal`` to avoid floating-point precision issues
  in accounting contexts.
* ``RiskCategory.PROBLEM_GAMBLING_RISK`` is not a punishment — it is a
  responsible-gaming signal that triggers enhanced monitoring, deposit-limit
  reviews, and eligibility restrictions.
* ``tags`` is an open list so that marketing and CRM systems can attach
  arbitrary labels (e.g. ``"churned_60d"``, ``"high_engagement"``) without
  schema migrations.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum, unique

from pydantic import BaseModel, ConfigDict, Field

from acmetocasino.gameservice.models.enums import ProductType  # ty: ignore[unresolved-import]


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


@unique
class SegmentTier(str, Enum):
    """Loyalty tier hierarchy, ordered from lowest to highest.

    ``BRONZE``
        New or low-activity players.
    ``SILVER``
        Regular players with moderate lifetime value.
    ``GOLD``
        Established, high-engagement players.
    ``PLATINUM``
        Premium players; dedicated account management may apply.
    ``VIP``
        Top tier; personalised service, exclusive bonuses.
    """

    BRONZE = "bronze"
    SILVER = "silver"
    GOLD = "gold"
    PLATINUM = "platinum"
    VIP = "vip"


@unique
class RiskCategory(str, Enum):
    """Player risk classification from a responsible-gaming perspective.

    ``LOW_RISK``
        No concerning patterns detected; standard monitoring applies.
    ``STANDARD``
        Normal activity level; default for most players.
    ``HIGH_VALUE``
        High lifetime value and engagement; warrant VIP-track treatment.
    ``PROBLEM_GAMBLING_RISK``
        Behavioural signals suggest potential problem gambling.  Enhanced
        responsible-gaming controls apply (e.g. mandatory deposit-limit
        review, reality-check reduction, outreach).
    """

    LOW_RISK = "low_risk"
    STANDARD = "standard"
    HIGH_VALUE = "high_value"
    PROBLEM_GAMBLING_RISK = "problem_gambling_risk"


# ---------------------------------------------------------------------------
# PlayerSegment
# ---------------------------------------------------------------------------


class PlayerSegment(BaseModel):
    """Canonical segmentation record for a single player.

    Attributes
    ----------
    player_id:
        Platform player identifier (UUID or opaque string).
    tier:
        Current loyalty tier.
    lifetime_deposits:
        Cumulative real-money deposit total in the platform's base currency.
    lifetime_wagered:
        Cumulative amount wagered across all games in the base currency.
    risk_category:
        Responsible-gaming risk classification.
    preferred_products:
        Game product categories the player has engaged with most.
    signup_date:
        The calendar date the player registered their account.
    last_active:
        UTC timestamp of the player's most recent activity event.
    tags:
        Arbitrary CRM / marketing labels attached to this player.
    """

    model_config = ConfigDict(frozen=True)

    player_id: str = Field(..., description="Platform player UUID")
    tier: SegmentTier = Field(
        default=SegmentTier.BRONZE,
        description="Current loyalty tier",
    )
    lifetime_deposits: Decimal = Field(
        default=Decimal("0"),
        ge=0,
        description="Cumulative deposit total (base currency)",
    )
    lifetime_wagered: Decimal = Field(
        default=Decimal("0"),
        ge=0,
        description="Cumulative wagered total (base currency)",
    )
    risk_category: RiskCategory = Field(
        default=RiskCategory.STANDARD,
        description="Responsible-gaming risk classification",
    )
    preferred_products: list[ProductType] = Field(
        default_factory=list,
        description="Preferred game product categories",
    )
    signup_date: date = Field(..., description="Account registration date")
    last_active: datetime = Field(..., description="UTC timestamp of last activity")
    tags: list[str] = Field(
        default_factory=list,
        description="Arbitrary CRM and marketing labels",
    )

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def is_high_risk(self) -> bool:
        """Return ``True`` if this player is flagged for problem gambling risk."""
        return self.risk_category == RiskCategory.PROBLEM_GAMBLING_RISK

    def has_tag(self, tag: str) -> bool:
        """Return ``True`` if *tag* is present in the player's tag list."""
        return tag in self.tags

    def tier_rank(self) -> int:
        """Return the numeric rank of the current tier (1 = lowest, 5 = highest)."""
        order = [
            SegmentTier.BRONZE,
            SegmentTier.SILVER,
            SegmentTier.GOLD,
            SegmentTier.PLATINUM,
            SegmentTier.VIP,
        ]
        return order.index(self.tier) + 1

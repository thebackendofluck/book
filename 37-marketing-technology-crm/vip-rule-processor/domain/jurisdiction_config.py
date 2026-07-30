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
Per-jurisdiction VIP programme configuration.
Chapter 37 — Marketing Technology and CRM

Python equivalent of JurisdictionConfig.scala.

Each jurisdiction imposes different requirements on VIP programmes:
- UK (UKGC): strict source-of-funds thresholds, mandatory GAMSTOP check
- Malta (MGA): higher thresholds, no mandatory self-exclusion register
- Sweden (SE): Spelinspektionen rules, Spelpaus integration required
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import ClassVar


class Jurisdiction(str, Enum):
    UKGC = "UKGC"
    MGA = "MGA"
    SE = "SE"


@dataclass(frozen=True)
class JurisdictionConfig:
    """
    Per-jurisdiction VIP programme configuration.

    Attributes
    ----------
    jurisdiction:
        Licensing jurisdiction.
    max_tier_without_sof:
        Maximum VIP tier level before a source-of-funds check is required.
        Players at or above this level must have SOF documentation cleared.
    sof_threshold_eur_cents:
        Cumulative deposit amount (in EUR cents) that triggers a SOF review.
    enhanced_dd_tier:
        Tier level at which enhanced due diligence (EDD) activates.
    cooling_period_days:
        Minimum number of days required between tier changes.
    vip_self_exclude_check:
        Whether to query the jurisdiction's self-exclusion register
        (GAMSTOP for UK, Spelpaus for Sweden) before VIP upgrade.
    """

    jurisdiction: Jurisdiction
    max_tier_without_sof: int      # max tier before SOF check required
    sof_threshold_eur_cents: int   # cumulative deposit triggering SOF review (cents)
    enhanced_dd_tier: int          # tier at which enhanced due diligence activates
    cooling_period_days: int       # minimum days between tier changes
    vip_self_exclude_check: bool   # whether to check self-exclusion registers

    # Pre-built configs
    # UK: stricter thresholds, mandatory self-exclusion check via GAMSTOP
    UK: ClassVar["JurisdictionConfig"]
    # Malta: higher thresholds, no mandatory self-exclusion register
    MGA: ClassVar["JurisdictionConfig"]
    # Sweden: Spelinspektionen rules, Spelpaus integration required
    SE: ClassVar["JurisdictionConfig"]

    ALL: ClassVar[dict[Jurisdiction, "JurisdictionConfig"]]

    @classmethod
    def for_jurisdiction(cls, j: Jurisdiction) -> "JurisdictionConfig | None":
        return cls.ALL.get(j)


# Module-level configuration singletons (populated after class definition)
JurisdictionConfig.UK = JurisdictionConfig(
    jurisdiction=Jurisdiction.UKGC,
    max_tier_without_sof=1,       # SOF required above VIP 1
    sof_threshold_eur_cents=200_000,  # GBP 2,000 cumulative (in cents)
    enhanced_dd_tier=2,            # EDD from VIP 2
    cooling_period_days=7,         # 7-day cooldown between changes
    vip_self_exclude_check=True,   # GAMSTOP integration required
)

JurisdictionConfig.MGA = JurisdictionConfig(
    jurisdiction=Jurisdiction.MGA,
    max_tier_without_sof=2,        # SOF required above VIP 2
    sof_threshold_eur_cents=1_000_000,  # EUR 10,000 cumulative (in cents)
    enhanced_dd_tier=3,             # EDD from VIP 3
    cooling_period_days=3,          # 3-day cooldown
    vip_self_exclude_check=False,   # no mandatory register
)

JurisdictionConfig.SE = JurisdictionConfig(
    jurisdiction=Jurisdiction.SE,
    max_tier_without_sof=1,
    sof_threshold_eur_cents=100_000,   # SEK ~10,000 equivalent
    enhanced_dd_tier=1,
    cooling_period_days=14,            # Spelpaus cooling-off respected
    vip_self_exclude_check=True,       # Spelpaus integration required
)

JurisdictionConfig.ALL = {
    Jurisdiction.UKGC: JurisdictionConfig.UK,
    Jurisdiction.MGA: JurisdictionConfig.MGA,
    Jurisdiction.SE: JurisdictionConfig.SE,
}

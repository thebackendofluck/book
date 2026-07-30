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
Pre-upgrade responsible gambling check.
Chapter 37 — Marketing Technology and CRM

Python equivalent of ResponsibleGamblingGuard.scala.

Queries the responsible gambling service before any tier upgrade is applied.
Returns Approved, Blocked (with reason), or RequiresReview (manual VIP
manager approval needed).

Pattern detection triggers RequiresReview rather than blocking outright,
satisfying UKGC requirement for human oversight of VIP decisions:
  - > 3 deposits within 1 hour
  - Deposit amounts increasing > 50% week-over-week
  - Deposits predominantly between 00:00 and 05:00 local time
  - Deposit frequency spike after large loss (chasing pattern)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Union


class BlockReason(str, Enum):
    SELF_EXCLUDED = "self_excluded"              # GAMSTOP / OASIS / Spelpaus
    COOLING_OFF_PERIOD = "cooling_off_period"    # voluntary cooling off
    DEPOSIT_LIMIT_REACHED = "deposit_limit_reached"  # hitting self-set limits
    EXCESSIVE_DEPOSIT_PATTERN = "excessive_deposit_pattern"  # algorithmic detection
    PENDING_SOF_REVIEW = "pending_sof_review"    # source of funds not verified
    AFFORDABILITY_CHECK_FAILED = "affordability_check_failed"  # UK affordability threshold


@dataclass(frozen=True)
class Approved:
    """Tier upgrade is permitted."""


@dataclass(frozen=True)
class Blocked:
    """Tier upgrade is blocked — player must not receive the upgrade."""

    reason: BlockReason


@dataclass(frozen=True)
class RequiresReview:
    """
    Tier upgrade requires manual approval by a VIP manager.

    The upgrade is not applied automatically; it is queued for human review.
    The reviewer field records the assigned manager's user ID or role name
    for audit trail purposes (UKGC requirement).
    """

    reason: str
    reviewer: str


UpgradeDecision = Union[Approved, Blocked, RequiresReview]


class ResponsibleGamblingGuard(ABC):
    """
    Abstract base for RG pre-upgrade checks.

    Implementations query the jurisdiction's self-exclusion register
    (GAMSTOP for UK, Spelpaus for SE), internal deposit-limit records,
    and the platform's pattern-detection engine.
    """

    @abstractmethod
    async def can_upgrade(
        self,
        user_id: int,
        brand_id: str,
        target_tier: int,
    ) -> UpgradeDecision:
        """
        Check whether the given user can be upgraded to *target_tier*.

        Parameters
        ----------
        user_id:
            Internal platform user identifier.
        brand_id:
            Brand/operator identifier.
        target_tier:
            The VIP tier level being requested.

        Returns
        -------
        Approved, Blocked, or RequiresReview.
        """
        ...


class StubResponsibleGamblingGuard(ResponsibleGamblingGuard):
    """
    Stub implementation for local development and testing.

    Always approves upgrades. Replace with a real implementation that
    queries GAMSTOP, Spelpaus, and internal deposit-limit records.
    """

    async def can_upgrade(
        self,
        user_id: int,
        brand_id: str,
        target_tier: int,
    ) -> UpgradeDecision:
        return Approved()

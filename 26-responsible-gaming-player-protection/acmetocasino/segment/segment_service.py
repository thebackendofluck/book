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
segment.segment_service — Segment Retrieval and Tier Recalculation
==================================================================

``SegmentService`` is the application-layer facade for all segment operations.
It owns the in-memory player segment store (suitable for tests and single-process
services) and contains the tier-recalculation logic.

Tier thresholds
---------------
Tier boundaries are configurable per brand and expressed as ``Decimal`` amounts
in the brand's base currency.  The defaults below reflect a EUR-denominated
brand:

+----------+---------------------+-----------------------+
| Tier     | Min Lifetime Dep.   | Min Lifetime Wagered  |
+==========+=====================+=======================+
| SILVER   | €  500              | €  2 500              |
+----------+---------------------+-----------------------+
| GOLD     | € 2 500             | € 12 500              |
+----------+---------------------+-----------------------+
| PLATINUM | € 10 000            | € 50 000              |
+----------+---------------------+-----------------------+
| VIP      | € 50 000            | € 250 000             |
+----------+---------------------+-----------------------+

The tier is determined by the **higher** of the two thresholds being met (i.e.
a player only needs to meet deposit *or* wagered criteria — whichever is more
favourable to the player is applied).

Thread safety
~~~~~~~~~~~~~
All mutations are protected by a ``threading.Lock``.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from acmetocasino.segment.player_segment import (
    PlayerSegment,
    RiskCategory,
    SegmentTier,
)


# ---------------------------------------------------------------------------
# Tier threshold configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TierThreshold:
    """Minimum requirements to attain a tier.

    A player attains *tier* if either ``min_lifetime_deposits`` **or**
    ``min_lifetime_wagered`` is met.

    Attributes
    ----------
    tier:
        The tier these thresholds unlock.
    min_lifetime_deposits:
        Minimum cumulative deposits required.
    min_lifetime_wagered:
        Minimum cumulative wagered amount required.
    """

    tier: SegmentTier
    min_lifetime_deposits: Decimal
    min_lifetime_wagered: Decimal


# Ordered from highest to lowest so the first match wins.
_DEFAULT_TIER_THRESHOLDS: list[TierThreshold] = [
    TierThreshold(
        SegmentTier.VIP,
        min_lifetime_deposits=Decimal("50000"),
        min_lifetime_wagered=Decimal("250000"),
    ),
    TierThreshold(
        SegmentTier.PLATINUM,
        min_lifetime_deposits=Decimal("10000"),
        min_lifetime_wagered=Decimal("50000"),
    ),
    TierThreshold(
        SegmentTier.GOLD,
        min_lifetime_deposits=Decimal("2500"),
        min_lifetime_wagered=Decimal("12500"),
    ),
    TierThreshold(
        SegmentTier.SILVER,
        min_lifetime_deposits=Decimal("500"),
        min_lifetime_wagered=Decimal("2500"),
    ),
]


# ---------------------------------------------------------------------------
# SegmentService
# ---------------------------------------------------------------------------


class SegmentService:
    """Retrieves and updates player segmentation records.

    Parameters
    ----------
    tier_thresholds:
        Ordered list of :class:`TierThreshold` objects (highest tier first).
        Defaults to ``_DEFAULT_TIER_THRESHOLDS``.

    Examples
    --------
    >>> from datetime import date, datetime
    >>> svc = SegmentService()
    >>> segment = svc.get_or_create("player-1")
    >>> segment.tier
    <SegmentTier.BRONZE: 'bronze'>
    """

    def __init__(
        self,
        *,
        tier_thresholds: list[TierThreshold] | None = None,
    ) -> None:
        self._thresholds = tier_thresholds or _DEFAULT_TIER_THRESHOLDS
        # player_id → PlayerSegment
        self._store: dict[str, PlayerSegment] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_segment(self, player_id: str) -> PlayerSegment:
        """Return the current segment for *player_id*.

        Parameters
        ----------
        player_id:
            Platform player identifier.

        Returns
        -------
        PlayerSegment
            The player's current segment record.

        Raises
        ------
        KeyError
            If *player_id* is not found in the store.  Use
            :meth:`get_or_create` to avoid this.
        """
        with self._lock:
            if player_id not in self._store:
                raise KeyError(f"Player {player_id!r} not found in segment store")
            return self._store[player_id]

    def get_or_create(self, player_id: str) -> PlayerSegment:
        """Return the segment for *player_id*, creating a BRONZE record if absent.

        Parameters
        ----------
        player_id:
            Platform player identifier.

        Returns
        -------
        PlayerSegment
            Existing or newly-created segment record.
        """
        with self._lock:
            if player_id not in self._store:
                now = datetime.now(tz=timezone.utc)
                self._store[player_id] = PlayerSegment(
                    player_id=player_id,
                    tier=SegmentTier.BRONZE,
                    signup_date=now.date(),
                    last_active=now,
                )
            return self._store[player_id]

    def update_segment(
        self,
        player_id: str,
        *,
        additional_deposits: Decimal = Decimal("0"),
        additional_wagered: Decimal = Decimal("0"),
        last_active: datetime | None = None,
        risk_category: RiskCategory | None = None,
        add_tags: list[str] | None = None,
        remove_tags: list[str] | None = None,
    ) -> PlayerSegment:
        """Apply incremental updates to a player's segment record.

        All parameters are optional; only the provided fields are changed.
        After applying updates, the tier is automatically recalculated.

        Parameters
        ----------
        player_id:
            Platform player identifier.
        additional_deposits:
            Amount to add to ``lifetime_deposits``.
        additional_wagered:
            Amount to add to ``lifetime_wagered``.
        last_active:
            Override the ``last_active`` timestamp.  Defaults to ``utcnow()``.
        risk_category:
            Explicit risk category override.
        add_tags:
            Tags to add (duplicates are ignored).
        remove_tags:
            Tags to remove (missing tags are silently ignored).

        Returns
        -------
        PlayerSegment
            The updated segment record (immutable snapshot).
        """
        with self._lock:
            existing = self._store.get(player_id)
            if existing is None:
                # Auto-create a baseline record.
                now = datetime.now(tz=timezone.utc)
                existing = PlayerSegment(
                    player_id=player_id,
                    tier=SegmentTier.BRONZE,
                    signup_date=now.date(),
                    last_active=now,
                )

            new_deposits = existing.lifetime_deposits + additional_deposits
            new_wagered = existing.lifetime_wagered + additional_wagered

            # Tag management
            current_tags = set(existing.tags)
            if add_tags:
                current_tags.update(add_tags)
            if remove_tags:
                current_tags.difference_update(remove_tags)

            # Recalculate tier based on new totals
            new_tier = self._calculate_tier(new_deposits, new_wagered)

            updated = existing.model_copy(
                update={
                    "tier": new_tier,
                    "lifetime_deposits": new_deposits,
                    "lifetime_wagered": new_wagered,
                    "last_active": last_active or datetime.now(tz=timezone.utc),
                    "risk_category": risk_category or existing.risk_category,
                    "tags": sorted(current_tags),
                }
            )
            self._store[player_id] = updated

        return updated

    def recalculate_tier(self, player_id: str) -> SegmentTier:
        """Recalculate and persist the tier for *player_id*.

        Useful when tier thresholds change and all players need re-evaluation.

        Parameters
        ----------
        player_id:
            Platform player identifier.

        Returns
        -------
        SegmentTier
            The newly-calculated (and saved) tier.

        Raises
        ------
        KeyError
            If *player_id* is not found in the store.
        """
        with self._lock:
            existing = self._store.get(player_id)
            if existing is None:
                raise KeyError(f"Player {player_id!r} not found in segment store")

            new_tier = self._calculate_tier(
                existing.lifetime_deposits,
                existing.lifetime_wagered,
            )
            if new_tier != existing.tier:
                self._store[player_id] = existing.model_copy(
                    update={"tier": new_tier}
                )

        return new_tier

    def store_segment(self, segment: PlayerSegment) -> None:
        """Directly persist a :class:`PlayerSegment` record.

        Intended for initialisation, migration, or test fixtures.

        Parameters
        ----------
        segment:
            The segment record to store.  Overwrites any existing record for
            ``segment.player_id``.
        """
        with self._lock:
            self._store[segment.player_id] = segment

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _calculate_tier(
        self,
        lifetime_deposits: Decimal,
        lifetime_wagered: Decimal,
    ) -> SegmentTier:
        """Return the highest tier for which the player meets the criteria.

        A player attains a tier if either their deposit *or* wagered total
        meets the threshold (the more favourable criterion applies).
        """
        for threshold in self._thresholds:
            if (
                lifetime_deposits >= threshold.min_lifetime_deposits
                or lifetime_wagered >= threshold.min_lifetime_wagered
            ):
                return threshold.tier
        return SegmentTier.BRONZE

# Companion code for "The Backend of Luck" - Chapter 10, Complete Platform Architecture.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
gameservice.accounts.bonus_service — BonusService
==================================================

Manages the full lifecycle of promotional bonuses:

1. **Allocation** — award a bonus amount with an associated wagering
   requirement (e.g. "35× the bonus amount must be wagered before
   withdrawal is permitted").
2. **Progress tracking** — record wagering contributions and check
   how much of the requirement has been fulfilled.
3. **Application** — apply bonus funds to a game round (reducing the
   remaining wagering requirement).
4. **Forfeiture** — allow the player (or the system) to void an uncompleted
   bonus, returning any associated funds to zero.

Wagering requirements in iGaming
---------------------------------
Most bonuses carry a "wagering requirement" (also called "playthrough
requirement").  A 35× requirement on a £10 bonus means the player must
place £350 in qualifying bets before the bonus converts to withdrawable cash.

Not all game types contribute equally:
* Slots typically contribute 100% of each bet toward the requirement.
* Live casino and table games often contribute 10–20%.
* Scratch cards may contribute 0%.

Contribution rates are looked up via
:class:`~acmetocasino.gameservice.accounts.balance_policy.BalancePolicy`.

In-memory implementation
------------------------
The in-memory store is suitable for unit tests.  Production deployments
need a persistent store with row-level locking to prevent race conditions
during concurrent bonus progress updates.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum, unique


# ---------------------------------------------------------------------------
# Bonus status
# ---------------------------------------------------------------------------


@unique
class BonusStatus(str, Enum):
    """Lifecycle state of a bonus allocation.

    ``ACTIVE``
        Bonus is allocated and available for wagering.
    ``COMPLETED``
        Wagering requirement has been met; funds are now withdrawable.
    ``FORFEITED``
        Bonus was voided before completion.
    ``EXPIRED``
        Bonus was not wagered within its validity window.
    """

    ACTIVE = "active"
    COMPLETED = "completed"
    FORFEITED = "forfeited"
    EXPIRED = "expired"


# ---------------------------------------------------------------------------
# Bonus record
# ---------------------------------------------------------------------------


@dataclass
class BonusAllocation:
    """An awarded bonus tracked through its wagering lifecycle.

    Attributes
    ----------
    bonus_id:
        Unique platform identifier for this allocation.
    player_id:
        The player who received the bonus.
    amount:
        The original awarded amount.
    bonus_type:
        Classifier string (e.g. ``"welcome"``, ``"reload"``, ``"free_spin"``).
    wagering_requirement:
        Multiplier applied to ``amount`` to determine the total wager target
        (e.g. ``Decimal("35")`` means 35× wagering).
    wagered_so_far:
        Running total of qualifying wagers applied to this bonus.
    status:
        Current lifecycle state.
    """

    bonus_id: str
    player_id: str
    amount: Decimal
    bonus_type: str
    wagering_requirement: Decimal
    wagered_so_far: Decimal = field(default_factory=lambda: Decimal("0"))
    status: BonusStatus = BonusStatus.ACTIVE

    @property
    def wager_target(self) -> Decimal:
        """Total wagering required to complete this bonus."""
        return self.amount * self.wagering_requirement

    @property
    def remaining_wager(self) -> Decimal:
        """Wagering amount still needed before conversion."""
        remaining = self.wager_target - self.wagered_so_far
        return max(Decimal("0"), remaining)

    @property
    def progress_pct(self) -> Decimal:
        """Completion percentage (0–100)."""
        if self.wager_target == Decimal("0"):
            return Decimal("100")
        pct = (self.wagered_so_far / self.wager_target) * Decimal("100")
        return min(Decimal("100"), pct)

    @property
    def is_active(self) -> bool:
        """Return True if the bonus can still be wagered against."""
        return self.status == BonusStatus.ACTIVE


# ---------------------------------------------------------------------------
# Progress result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WageringProgress:
    """Snapshot of a bonus's wagering progress.

    Returned by :meth:`BonusService.check_wagering_progress`.

    Attributes
    ----------
    bonus_id:
        The bonus identifier.
    status:
        Current lifecycle state.
    amount:
        Original bonus amount.
    wager_target:
        Total wager required to unlock the bonus.
    wagered_so_far:
        Qualifying wagers applied so far.
    remaining_wager:
        How much more needs to be wagered.
    progress_pct:
        Completion percentage (0–100).
    """

    bonus_id: str
    status: BonusStatus
    amount: Decimal
    wager_target: Decimal
    wagered_so_far: Decimal
    remaining_wager: Decimal
    progress_pct: Decimal


# ---------------------------------------------------------------------------
# BonusService
# ---------------------------------------------------------------------------


class BonusService:
    """Manages the bonus allocation and wagering lifecycle.

    This implementation stores state in-memory.  Replace the internal
    ``_bonuses`` dict with a persistent store before deploying to production.

    Parameters
    ----------
    default_wagering_multiplier:
        Fallback wagering requirement used when no explicit multiplier is
        passed to :meth:`allocate_bonus`.  Typical iGaming value: 35.
    """

    def __init__(self, default_wagering_multiplier: Decimal = Decimal("35")) -> None:
        self._default_multiplier = default_wagering_multiplier
        self._bonuses: dict[str, BonusAllocation] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Allocation
    # ------------------------------------------------------------------

    def allocate_bonus(
        self,
        player_id: str,
        amount: Decimal,
        bonus_type: str,
        wagering_requirement: Decimal | None = None,
    ) -> BonusAllocation:
        """Create a new bonus allocation for *player_id*.

        Parameters
        ----------
        player_id:
            Target player.
        amount:
            Bonus amount to award.  Must be > 0.
        bonus_type:
            Classifier string (e.g. ``"welcome"``, ``"reload"``).
        wagering_requirement:
            The multiplier (e.g. ``Decimal("35")`` means 35×).  Falls back
            to the service's ``default_wagering_multiplier`` if ``None``.

        Returns
        -------
        BonusAllocation
            The newly created allocation record.

        Raises
        ------
        ValueError
            If *amount* is non-positive.
        """
        if amount <= Decimal("0"):
            raise ValueError(f"Bonus amount must be positive, got {amount!r}")

        multiplier = wagering_requirement if wagering_requirement is not None else self._default_multiplier
        bonus_id = str(uuid.uuid4())
        allocation = BonusAllocation(
            bonus_id=bonus_id,
            player_id=player_id,
            amount=amount,
            bonus_type=bonus_type,
            wagering_requirement=multiplier,
        )
        with self._lock:
            self._bonuses[bonus_id] = allocation
        return allocation

    # ------------------------------------------------------------------
    # Progress
    # ------------------------------------------------------------------

    def check_wagering_progress(self, player_id: str, bonus_id: str) -> WageringProgress:
        """Return the current wagering progress for *bonus_id*.

        Parameters
        ----------
        player_id:
            The owning player (used for ownership assertion).
        bonus_id:
            The bonus to inspect.

        Returns
        -------
        WageringProgress
            Snapshot of completion state.

        Raises
        ------
        KeyError
            If *bonus_id* is unknown.
        PermissionError
            If *bonus_id* belongs to a different player.
        """
        with self._lock:
            allocation = self._get_and_assert(player_id, bonus_id)
        return WageringProgress(
            bonus_id=allocation.bonus_id,
            status=allocation.status,
            amount=allocation.amount,
            wager_target=allocation.wager_target,
            wagered_so_far=allocation.wagered_so_far,
            remaining_wager=allocation.remaining_wager,
            progress_pct=allocation.progress_pct,
        )

    # ------------------------------------------------------------------
    # Forfeit
    # ------------------------------------------------------------------

    def forfeit_bonus(self, player_id: str, bonus_id: str) -> BonusAllocation:
        """Void an active bonus before its wagering requirement is met.

        Parameters
        ----------
        player_id:
            The owning player.
        bonus_id:
            The bonus to forfeit.

        Returns
        -------
        BonusAllocation
            Updated allocation record with ``status=FORFEITED``.

        Raises
        ------
        KeyError
            If *bonus_id* is unknown.
        ValueError
            If the bonus is not in ``ACTIVE`` status.
        """
        with self._lock:
            allocation = self._get_and_assert(player_id, bonus_id)
            if allocation.status != BonusStatus.ACTIVE:
                raise ValueError(
                    f"Cannot forfeit bonus {bonus_id!r} in status {allocation.status.value!r}"
                )
            allocation.status = BonusStatus.FORFEITED
        return allocation

    # ------------------------------------------------------------------
    # Wager contribution
    # ------------------------------------------------------------------

    def apply_bonus_to_round(
        self,
        player_id: str,
        round_id: str,
        bonus_id: str,
        contribution_amount: Decimal,
    ) -> BonusAllocation:
        """Record a wagering contribution against an active bonus.

        Called by the balance policy after each qualifying wager to advance
        the player's progress toward the completion threshold.

        Parameters
        ----------
        player_id:
            The owning player.
        round_id:
            Supplier round identifier (for audit purposes; not stored here).
        bonus_id:
            The bonus to advance.
        contribution_amount:
            The qualifying wager amount to add to ``wagered_so_far``.
            This is typically the raw wager × the game's contribution rate.

        Returns
        -------
        BonusAllocation
            Updated allocation.  Check ``status`` to see if it transitioned
            to ``COMPLETED``.

        Raises
        ------
        KeyError
            If *bonus_id* is unknown.
        ValueError
            If the bonus is not ``ACTIVE``.
        """
        if contribution_amount < Decimal("0"):
            raise ValueError(
                f"Contribution amount must be non-negative, got {contribution_amount!r}"
            )
        with self._lock:
            allocation = self._get_and_assert(player_id, bonus_id)
            if not allocation.is_active:
                raise ValueError(
                    f"Cannot apply contribution to bonus {bonus_id!r} "
                    f"in status {allocation.status.value!r}"
                )
            allocation.wagered_so_far += contribution_amount
            # Auto-complete when threshold is reached.
            if allocation.wagered_so_far >= allocation.wager_target:
                allocation.status = BonusStatus.COMPLETED
        return allocation

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_and_assert(self, player_id: str, bonus_id: str) -> BonusAllocation:
        """Fetch a bonus record and assert ownership."""
        allocation = self._bonuses.get(bonus_id)
        if allocation is None:
            raise KeyError(f"Unknown bonus_id: {bonus_id!r}")
        if allocation.player_id != player_id:
            raise PermissionError(
                f"Bonus {bonus_id!r} does not belong to player {player_id!r}"
            )
        return allocation

    def active_bonuses_for(self, player_id: str) -> list[BonusAllocation]:
        """Return all active bonus allocations for *player_id*."""
        with self._lock:
            return [
                b for b in self._bonuses.values()
                if b.player_id == player_id and b.is_active
            ]


__all__ = [
    "BonusAllocation",
    "BonusService",
    "BonusStatus",
    "WageringProgress",
]

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
Cooldown guard — prevents rapid tier oscillation.
Chapter 37 — Marketing Technology and CRM

Python equivalent of CooldownGuard.scala.

If a player is within the cooldown window, the new tier is recorded as
`pending_tier` in the database but not activated. The midnight batch picks
up pending tiers that have cleared their cooldown and applies them.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


class CooldownGuard:
    """
    Determines whether a tier change is permitted given the cooldown policy.

    The guard is stateless: pass in the last-change timestamp and the
    cooldown period and it returns immediately.
    """

    @staticmethod
    def can_change_tier(
        last_change: datetime | None,
        cooldown_days: int,
        now: datetime | None = None,
    ) -> bool:
        """
        Return True if a tier change is permitted, False if still within cooldown.

        Parameters
        ----------
        last_change:
            Timestamp of the last tier change. None for new accounts
            (always permitted).
        cooldown_days:
            Minimum days required between tier changes (jurisdiction-specific).
            UKGC = 7, MGA = 3, SE = 14.
        now:
            Current time. Defaults to UTC now. Accepts timezone-aware or
            naive datetimes; if naive, treated as UTC.

        Returns
        -------
        True if the cooldown period has elapsed (or no previous change exists).
        """
        if last_change is None:
            # New account: no cooldown constraint
            return True

        if now is None:
            now = datetime.now(timezone.utc)

        # Normalise to naive UTC for comparison if both are naive
        _now = now.replace(tzinfo=None) if now.tzinfo is not None else now
        _last = last_change.replace(tzinfo=None) if last_change.tzinfo is not None else last_change

        elapsed_days = (_now - _last).days
        return elapsed_days >= cooldown_days

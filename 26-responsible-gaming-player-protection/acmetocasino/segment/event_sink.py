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
segment.event_sink — Activity Event Accumulator
================================================

``SegmentEventSink`` listens to platform activity events and translates them
into segment updates.  It is the bridge between the game service / wallet
service and the segmentation system.

Event model
-----------
The sink accumulates metrics per player and triggers tier recalculation after
each event.  In a production system you would wire this up to a Kafka consumer
or an async event bus; in tests, call the methods directly.

Activity tracking
-----------------
Each event type contributes to the player's running metrics:

* ``on_deposit`` — increments ``lifetime_deposits``
* ``on_withdrawal`` — no effect on tier; logged for monitoring
* ``on_round_completed`` — increments ``lifetime_wagered``
* ``on_session_ended`` — updates ``last_active``; checks for extended session
  patterns that may indicate responsible-gaming risk

Responsible gaming signals
--------------------------
The sink also watches for patterns that suggest a player should be elevated
to ``PROBLEM_GAMBLING_RISK``:

* Session duration >= 4 hours (configurable)
* More than *n* deposits in a single session (configurable)

These rules are intentionally simple; a real implementation would use a
statistical model.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from acmetocasino.segment.player_segment import RiskCategory
from acmetocasino.segment.segment_service import SegmentService


# ---------------------------------------------------------------------------
# Session accumulator (internal)
# ---------------------------------------------------------------------------


@dataclass
class _SessionAccumulator:
    """Tracks metrics for the current (or most recent) session."""

    started_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    deposit_count: int = 0
    total_deposited: Decimal = Decimal("0")
    total_wagered: Decimal = Decimal("0")
    round_count: int = 0


# ---------------------------------------------------------------------------
# SegmentEventSink
# ---------------------------------------------------------------------------


class SegmentEventSink:
    """Accumulates player activity events and drives segment updates.

    Parameters
    ----------
    segment_service:
        The :class:`~acmetocasino.segment.segment_service.SegmentService`
        instance to update when events are received.
    long_session_threshold_minutes:
        Sessions longer than this value trigger a responsible-gaming risk
        review.  Default: 240 (4 hours).
    session_deposit_limit:
        If a player makes more than this many deposits in a single session,
        a responsible-gaming signal is raised.  Default: 5.

    Examples
    --------
    >>> svc = SegmentService()
    >>> sink = SegmentEventSink(svc)
    >>> sink.on_deposit("player-1", Decimal("100"))
    >>> sink.on_round_completed("player-1", "book-of-dead", Decimal("10"), Decimal("0"))
    >>> sink.on_session_ended("player-1", duration_minutes=30)
    """

    def __init__(
        self,
        segment_service: SegmentService,
        *,
        long_session_threshold_minutes: int = 240,
        session_deposit_limit: int = 5,
    ) -> None:
        self._service = segment_service
        self._long_session_threshold = long_session_threshold_minutes
        self._session_deposit_limit = session_deposit_limit

        # player_id → _SessionAccumulator
        self._sessions: dict[str, _SessionAccumulator] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public event handlers
    # ------------------------------------------------------------------

    def on_deposit(self, player_id: str, amount: Decimal) -> None:
        """Record a successful deposit for *player_id*.

        Increments the player's ``lifetime_deposits`` and checks whether
        the session deposit count warrants a responsible-gaming flag.

        Parameters
        ----------
        player_id:
            Platform player identifier.
        amount:
            Deposit amount (positive, in base currency).
        """
        if amount <= Decimal("0"):
            raise ValueError(f"Deposit amount must be positive; got {amount!r}")

        with self._lock:
            acc = self._get_or_create_accumulator(player_id)
            acc.deposit_count += 1
            acc.total_deposited += amount

            # Responsible gaming signal: many deposits in one session.
            should_flag_rg = acc.deposit_count > self._session_deposit_limit

        # Update segment outside the lock to avoid holding it during I/O.
        self._service.update_segment(
            player_id,
            additional_deposits=amount,
        )

        if should_flag_rg:
            self._elevate_risk_if_needed(
                player_id,
                f"Exceeded {self._session_deposit_limit} deposits in one session",
            )

    def on_withdrawal(self, player_id: str, amount: Decimal) -> None:
        """Record a withdrawal for *player_id*.

        Withdrawals do not affect tier metrics but are tracked for monitoring
        and for potential withdrawal velocity checks downstream.

        Parameters
        ----------
        player_id:
            Platform player identifier.
        amount:
            Withdrawal amount (positive, in base currency).
        """
        if amount <= Decimal("0"):
            raise ValueError(f"Withdrawal amount must be positive; got {amount!r}")

        # Update last_active timestamp.
        self._service.update_segment(player_id)

    def on_round_completed(
        self,
        player_id: str,
        game_id: str,  # noqa: ARG002 — available for future per-game analytics
        wager: Decimal,
        payout: Decimal,  # noqa: ARG002 — available for future win-rate analytics
    ) -> None:
        """Record a completed game round for *player_id*.

        Increments ``lifetime_wagered`` by *wager*.  Winnings do not count
        toward lifetime wagered (only the debit side does).

        Parameters
        ----------
        player_id:
            Platform player identifier.
        game_id:
            The game that was played (for future per-game analytics).
        wager:
            The amount wagered in this round (positive, in base currency).
        payout:
            The amount paid out (for future retention of win-rate metrics).
        """
        if wager < Decimal("0"):
            raise ValueError(f"Wager must be non-negative; got {wager!r}")

        with self._lock:
            acc = self._get_or_create_accumulator(player_id)
            acc.total_wagered += wager
            acc.round_count += 1

        if wager > Decimal("0"):
            self._service.update_segment(player_id, additional_wagered=wager)
        else:
            self._service.update_segment(player_id)

    def on_session_ended(
        self,
        player_id: str,
        duration_minutes: int,
    ) -> None:
        """Record the end of a play session for *player_id*.

        Updates ``last_active`` and checks whether the session duration
        warrants a responsible-gaming risk escalation.

        Parameters
        ----------
        player_id:
            Platform player identifier.
        duration_minutes:
            Total length of the session in minutes.
        """
        with self._lock:
            # Remove the accumulator — the session is over.
            self._sessions.pop(player_id, None)

        self._service.update_segment(
            player_id,
            last_active=datetime.now(tz=timezone.utc),
        )

        if duration_minutes >= self._long_session_threshold:
            self._elevate_risk_if_needed(
                player_id,
                f"Session duration {duration_minutes}min exceeded threshold "
                f"{self._long_session_threshold}min",
            )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_or_create_accumulator(self, player_id: str) -> _SessionAccumulator:
        """Return the active session accumulator for *player_id* (must hold lock)."""
        if player_id not in self._sessions:
            self._sessions[player_id] = _SessionAccumulator()
        return self._sessions[player_id]

    def _elevate_risk_if_needed(self, player_id: str, reason: str) -> None:
        """Escalate *player_id* to PROBLEM_GAMBLING_RISK if not already there.

        This is a one-way escalation — we never automatically de-escalate back
        to STANDARD.  De-escalation requires a human compliance review.
        """
        try:
            segment = self._service.get_segment(player_id)
        except KeyError:
            return  # Player doesn't exist yet; nothing to escalate.

        if segment.risk_category != RiskCategory.PROBLEM_GAMBLING_RISK:
            self._service.update_segment(
                player_id,
                risk_category=RiskCategory.PROBLEM_GAMBLING_RISK,
                add_tags=[f"rg_flag:{reason[:64]}"],
            )

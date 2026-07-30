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
gameservice.accounts.limits_service — LimitsService
=====================================================

Enforces responsible-gambling and operator-mandated financial limits before
transactions are applied to a player's wallet.

Limit types
-----------
``deposit``
    Maximum amount the player may deposit in a rolling period (daily, weekly,
    monthly).  Usually self-imposed but may also be operator-mandated.

``loss``
    Maximum net loss the player may incur in a rolling period.  Required in
    some jurisdictions (e.g. Sweden, UK).

``wager``
    Maximum amount the player may stake in a single bet or in a rolling
    period.  Some regulators (e.g. Germany) cap individual bets at €1.

``session_duration``
    Maximum wall-clock time the player may spend in a single session before
    being forced to take a break.

Each check returns a :class:`LimitCheckResult` so the caller can either
block the operation or display a warning without raising an exception.

In-memory implementation
------------------------
Limits and usage counters are stored in-memory for testing.  Production
requires a persistent store with atomic counter increments (e.g. Redis
``INCRBYFLOAT`` or a database with row-level locks).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from decimal import Decimal


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LimitCheckResult:
    """Outcome of a single limit evaluation.

    Attributes
    ----------
    allowed:
        ``True`` if the requested amount is within the player's limits.
    reason:
        Human-readable explanation when ``allowed=False``.
    remaining:
        How much more the player is permitted to spend in the current period.
        ``None`` when the limit type does not have a meaningful remaining
        concept (e.g. session-duration check).
    limit_type:
        The kind of limit that was checked (``"deposit"``, ``"loss"``,
        ``"wager"``, ``"session_duration"``).
    """

    allowed: bool
    reason: str | None = None
    remaining: Decimal | None = None
    limit_type: str = "unknown"

    @classmethod
    def ok(cls, limit_type: str, remaining: Decimal | None = None) -> LimitCheckResult:
        """Convenience constructor for a passing check."""
        return cls(allowed=True, limit_type=limit_type, remaining=remaining)

    @classmethod
    def blocked(
        cls,
        limit_type: str,
        reason: str,
        remaining: Decimal | None = None,
    ) -> LimitCheckResult:
        """Convenience constructor for a failing check."""
        return cls(allowed=False, reason=reason, limit_type=limit_type, remaining=remaining)


# ---------------------------------------------------------------------------
# Limit definition
# ---------------------------------------------------------------------------


@dataclass
class _LimitRecord:
    """Internal state for a player/type/period limit."""

    limit_amount: Decimal
    period_seconds: int
    used: Decimal = field(default_factory=lambda: Decimal("0"))
    period_started_at: float = field(default_factory=time.monotonic)

    def reset_if_expired(self) -> None:
        """Reset used counter if the current period has elapsed."""
        elapsed = time.monotonic() - self.period_started_at
        if elapsed >= self.period_seconds:
            self.used = Decimal("0")
            self.period_started_at = time.monotonic()

    @property
    def remaining(self) -> Decimal:
        return max(Decimal("0"), self.limit_amount - self.used)


# ---------------------------------------------------------------------------
# LimitsService
# ---------------------------------------------------------------------------


class LimitsService:
    """Evaluates financial and session limits before wallet operations.

    Parameters
    ----------
    default_session_duration_seconds:
        The fallback maximum session duration (in seconds) when no
        player-specific limit is set.  ``0`` disables session-duration
        checks by default.
    """

    def __init__(self, default_session_duration_seconds: int = 0) -> None:
        self._default_session_secs = default_session_duration_seconds
        # Keyed by (player_id, limit_type)
        self._limits: dict[tuple[str, str], _LimitRecord] = {}
        # Session start times keyed by player_id
        self._session_start: dict[str, float] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Limit registration
    # ------------------------------------------------------------------

    def set_limit(
        self,
        player_id: str,
        limit_type: str,
        amount: Decimal,
        period_seconds: int,
    ) -> None:
        """Register or update a financial limit for a player.

        Parameters
        ----------
        player_id:
            Target player.
        limit_type:
            One of ``"deposit"``, ``"loss"``, ``"wager"``.
        amount:
            The maximum amount permitted in the period.
        period_seconds:
            Rolling-window duration in seconds.  Common values:
            * 86400  — daily
            * 604800 — weekly
            * 2592000 — monthly (30 days)
        """
        key = (player_id, limit_type)
        with self._lock:
            self._limits[key] = _LimitRecord(
                limit_amount=amount,
                period_seconds=period_seconds,
            )

    def record_session_start(self, player_id: str) -> None:
        """Mark the start of a new session for session-duration tracking."""
        with self._lock:
            self._session_start[player_id] = time.monotonic()

    # ------------------------------------------------------------------
    # Checks
    # ------------------------------------------------------------------

    def check_deposit_limit(
        self,
        player_id: str,
        amount: Decimal,
    ) -> LimitCheckResult:
        """Check whether *amount* would exceed the player's deposit limit.

        Parameters
        ----------
        player_id:
            Target player.
        amount:
            The deposit amount to evaluate.

        Returns
        -------
        LimitCheckResult
            ``allowed=True`` if no limit is set or the amount fits;
            ``allowed=False`` with a descriptive ``reason`` if blocked.
        """
        return self._check_financial("deposit", player_id, amount)

    def check_loss_limit(
        self,
        player_id: str,
        amount: Decimal,
    ) -> LimitCheckResult:
        """Check whether *amount* would exceed the player's loss limit.

        Loss limits accumulate net losses: for each settled game round,
        the net loss (debit − credit) should be reported via
        :meth:`record_usage` before the next check.

        Parameters
        ----------
        player_id:
            Target player.
        amount:
            The net loss amount of the round being evaluated.

        Returns
        -------
        LimitCheckResult
        """
        return self._check_financial("loss", player_id, amount)

    def check_wager_limit(
        self,
        player_id: str,
        amount: Decimal,
    ) -> LimitCheckResult:
        """Check whether *amount* is within the player's single-wager limit.

        Parameters
        ----------
        player_id:
            Target player.
        amount:
            The wager amount to evaluate.

        Returns
        -------
        LimitCheckResult
        """
        return self._check_financial("wager", player_id, amount)

    def check_session_duration(self, player_id: str) -> LimitCheckResult:
        """Check whether the player has exceeded their session-duration limit.

        Parameters
        ----------
        player_id:
            Target player.

        Returns
        -------
        LimitCheckResult
            ``allowed=True`` if no limit is set or the session is still
            within the allowed window.
        """
        with self._lock:
            start = self._session_start.get(player_id)
            player_limit_key = (player_id, "session_duration")
            player_limit = self._limits.get(player_limit_key)

        if start is None:
            # Session not tracked yet — allow but note the anomaly.
            return LimitCheckResult.ok("session_duration")

        # Determine the applicable limit.
        if player_limit is not None:
            max_secs = player_limit.period_seconds
        elif self._default_session_secs > 0:
            max_secs = self._default_session_secs
        else:
            # No limit configured.
            return LimitCheckResult.ok("session_duration")

        elapsed_secs = time.monotonic() - start
        if elapsed_secs >= max_secs:
            elapsed_min = int(elapsed_secs / 60)
            max_min = int(max_secs / 60)
            return LimitCheckResult.blocked(
                "session_duration",
                reason=(
                    f"Session duration {elapsed_min} min exceeds limit of {max_min} min"
                ),
            )
        remaining_secs = max_secs - elapsed_secs
        return LimitCheckResult.ok(
            "session_duration",
            remaining=Decimal(str(round(remaining_secs, 0))),
        )

    # ------------------------------------------------------------------
    # Usage recording
    # ------------------------------------------------------------------

    def record_usage(
        self,
        player_id: str,
        limit_type: str,
        amount: Decimal,
    ) -> None:
        """Accumulate *amount* against the player's limit for *limit_type*.

        Call this after a transaction is confirmed.  The service maintains
        rolling-window counters and resets them when the period elapses.

        Parameters
        ----------
        player_id:
            Target player.
        limit_type:
            One of ``"deposit"``, ``"loss"``, ``"wager"``.
        amount:
            The amount to accumulate.
        """
        key = (player_id, limit_type)
        with self._lock:
            record = self._limits.get(key)
            if record is not None:
                record.reset_if_expired()
                record.used += amount

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _check_financial(
        self,
        limit_type: str,
        player_id: str,
        amount: Decimal,
    ) -> LimitCheckResult:
        key = (player_id, limit_type)
        with self._lock:
            record = self._limits.get(key)
            if record is None:
                return LimitCheckResult.ok(limit_type)
            record.reset_if_expired()
            if record.used + amount > record.limit_amount:
                return LimitCheckResult.blocked(
                    limit_type,
                    reason=(
                        f"{limit_type.capitalize()} limit of {record.limit_amount} "
                        f"would be exceeded (used={record.used}, requested={amount})"
                    ),
                    remaining=record.remaining,
                )
            return LimitCheckResult.ok(limit_type, remaining=record.remaining)


__all__ = ["LimitCheckResult", "LimitsService"]

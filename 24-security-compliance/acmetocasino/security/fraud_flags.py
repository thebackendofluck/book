# Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
fraud_flags — Velocity Checks and Risk Scoring
================================================

``FraudFlags`` provides a first line of defence against abnormal player
behaviour.  It is intentionally lightweight — a real production system would
back this with a dedicated risk-scoring service (e.g. Sift, Featurespace,
or a home-built ML pipeline).  This module serves as the integration shim
and an in-process fallback for tests.

Capabilities
------------
* **Velocity checks** — sliding window counters per (player, action_type).
  Breaching a threshold raises the risk level.
* **Amount anomaly detection** — flags debit/credit amounts that deviate
  significantly from a player's historical average.
* **Manual flagging** — allows compliance staff (or other services) to
  annotate a player with a reason and severity.
* **Risk score** — aggregates all signals into a single ``RiskScore`` object.

Thread safety
~~~~~~~~~~~~~
All mutable state is protected by a ``threading.Lock``.  This is suitable for
a multi-threaded WSGI process; use ``asyncio.Lock`` in async contexts.
"""

from __future__ import annotations

import threading
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum, unique


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


@unique
class RiskLevel(str, Enum):
    """Categorical risk severity.

    ``LOW``
        No unusual signals detected.
    ``MEDIUM``
        One or more minor anomalies; warrant monitoring.
    ``HIGH``
        Multiple or significant anomalies; warrant manual review.
    ``CRITICAL``
        Severe signals; automatic restrictions should be applied.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ---------------------------------------------------------------------------
# Result / value types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FraudCheckResult:
    """Outcome of a single fraud check.

    Attributes
    ----------
    allowed:
        ``True`` if the action may proceed under current risk thresholds.
    flags:
        List of human-readable flag codes explaining any concerns.
    risk_level:
        The highest risk level encountered during this check.
    """

    allowed: bool
    flags: list[str]
    risk_level: RiskLevel


@dataclass(frozen=True)
class RiskScore:
    """Aggregated risk assessment for a player.

    Attributes
    ----------
    player_id:
        The assessed player.
    score:
        Numeric score in the range ``[0.0, 1.0]``.  Values >= 0.75 are
        considered ``CRITICAL``.
    level:
        Categorical risk level derived from *score*.
    active_flags:
        All currently active flag codes for this player.
    manual_flags:
        Flags set manually via :meth:`FraudFlags.flag_suspicious`.
    """

    player_id: str
    score: float
    level: RiskLevel
    active_flags: list[str]
    manual_flags: list[str]


# ---------------------------------------------------------------------------
# Internal records
# ---------------------------------------------------------------------------


@dataclass
class _VelocityWindow:
    """Sliding-window counter for an (player_id, action_type) pair."""

    events: deque[datetime] = field(default_factory=deque)


@dataclass
class _ManualFlag:
    """A manually-set fraud flag."""

    reason: str
    severity: RiskLevel
    flagged_at: datetime


@dataclass
class _AmountHistory:
    """Running statistics for amount anomaly detection."""

    samples: deque[Decimal] = field(default_factory=deque)
    total: Decimal = Decimal("0")

    def add(self, amount: Decimal, *, max_samples: int = 100) -> None:
        if len(self.samples) >= max_samples:
            oldest = self.samples.popleft()
            self.total -= oldest
        self.samples.append(amount)
        self.total += amount

    def mean(self) -> Decimal | None:
        if not self.samples:
            return None
        return self.total / Decimal(len(self.samples))


# ---------------------------------------------------------------------------
# Velocity and anomaly thresholds
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VelocityThreshold:
    """Configuration for one velocity rule.

    Attributes
    ----------
    action_type:
        The action this threshold applies to (e.g. ``"deposit"``, ``"bet"``).
    max_count:
        Maximum allowed events within *window_seconds*.
    window_seconds:
        Rolling window duration in seconds.
    risk_level:
        Risk level assigned when the threshold is breached.
    """

    action_type: str
    max_count: int
    window_seconds: int
    risk_level: RiskLevel


_DEFAULT_VELOCITY_RULES: list[VelocityThreshold] = [
    VelocityThreshold("deposit", max_count=10, window_seconds=3600, risk_level=RiskLevel.MEDIUM),
    VelocityThreshold("deposit", max_count=20, window_seconds=3600, risk_level=RiskLevel.HIGH),
    VelocityThreshold("withdrawal", max_count=5, window_seconds=3600, risk_level=RiskLevel.MEDIUM),
    VelocityThreshold("bet", max_count=500, window_seconds=60, risk_level=RiskLevel.MEDIUM),
    VelocityThreshold("bet", max_count=1000, window_seconds=60, risk_level=RiskLevel.HIGH),
    VelocityThreshold("login", max_count=10, window_seconds=300, risk_level=RiskLevel.HIGH),
]

# An amount is anomalous if it exceeds this multiple of the player's mean.
_ANOMALY_MULTIPLIER = Decimal("5")
# Minimum number of historical samples before anomaly detection kicks in.
_MIN_HISTORY_SAMPLES = 5


# ---------------------------------------------------------------------------
# FraudFlags
# ---------------------------------------------------------------------------


class FraudFlags:
    """Lightweight in-process fraud detection engine.

    Parameters
    ----------
    velocity_rules:
        List of :class:`VelocityThreshold` rules.  Defaults to
        ``_DEFAULT_VELOCITY_RULES``.
    anomaly_multiplier:
        Threshold multiplier for amount anomaly detection.  An amount is
        flagged if it exceeds ``mean * anomaly_multiplier``.  Default: 5.
    block_on_critical:
        If ``True``, ``check_velocity`` and ``check_amount_anomaly`` return
        ``allowed=False`` when the risk level reaches ``CRITICAL``.
        Default: ``True``.

    Examples
    --------
    >>> ff = FraudFlags()
    >>> result = ff.check_velocity("player-1", "bet")
    >>> result.allowed
    True
    >>> result.risk_level
    <RiskLevel.LOW: 'low'>
    """

    def __init__(
        self,
        *,
        velocity_rules: list[VelocityThreshold] | None = None,
        anomaly_multiplier: Decimal = _ANOMALY_MULTIPLIER,
        block_on_critical: bool = True,
    ) -> None:
        self._rules = velocity_rules or _DEFAULT_VELOCITY_RULES
        self._anomaly_multiplier = anomaly_multiplier
        self._block_on_critical = block_on_critical

        # (player_id, action_type) → _VelocityWindow
        self._velocity: dict[tuple[str, str], _VelocityWindow] = defaultdict(
            _VelocityWindow
        )
        # player_id → list of _ManualFlag
        self._manual_flags: dict[str, list[_ManualFlag]] = defaultdict(list)
        # (player_id, action_type) → _AmountHistory
        self._amount_history: dict[tuple[str, str], _AmountHistory] = defaultdict(
            _AmountHistory
        )
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_velocity(
        self,
        player_id: str,
        action_type: str,
    ) -> FraudCheckResult:
        """Record an action event and evaluate velocity rules.

        This method both **records** the event and **evaluates** whether the
        current rate of *action_type* events for *player_id* breaches any
        configured threshold.

        Parameters
        ----------
        player_id:
            The player performing the action.
        action_type:
            Short string identifying the action (e.g. ``"bet"``, ``"deposit"``).

        Returns
        -------
        FraudCheckResult
            Outcome including any triggered flags and the resulting risk level.
        """
        now = datetime.now(tz=timezone.utc)
        flags: list[str] = []
        highest_level = RiskLevel.LOW

        with self._lock:
            key = (player_id, action_type)
            window = self._velocity[key]
            window.events.append(now)

            # Evaluate all rules for this action_type
            relevant_rules = [r for r in self._rules if r.action_type == action_type]
            for rule in relevant_rules:
                cutoff = now - timedelta(seconds=rule.window_seconds)
                count = sum(1 for ts in window.events if ts >= cutoff)
                if count > rule.max_count:
                    flag_code = (
                        f"velocity:{action_type}:{rule.max_count}/"
                        f"{rule.window_seconds}s"
                    )
                    flags.append(flag_code)
                    if self._level_order(rule.risk_level) > self._level_order(highest_level):
                        highest_level = rule.risk_level

            # Prune events older than the longest relevant window
            if relevant_rules:
                max_window = max(r.window_seconds for r in relevant_rules)
                oldest_cutoff = now - timedelta(seconds=max_window)
                while window.events and window.events[0] < oldest_cutoff:
                    window.events.popleft()

        allowed = not (
            self._block_on_critical and highest_level == RiskLevel.CRITICAL
        )
        return FraudCheckResult(
            allowed=allowed,
            flags=flags,
            risk_level=highest_level,
        )

    def check_amount_anomaly(
        self,
        player_id: str,
        amount: Decimal,
        action_type: str,
    ) -> FraudCheckResult:
        """Record *amount* for *action_type* and detect statistical anomalies.

        An amount is considered anomalous if it exceeds ``mean * anomaly_multiplier``
        based on the player's recent history for the same action type.  Detection
        is skipped until at least ``_MIN_HISTORY_SAMPLES`` samples have been
        collected, to avoid false positives at account creation time.

        Parameters
        ----------
        player_id:
            The player performing the transaction.
        amount:
            Transaction amount (must be positive).
        action_type:
            Type of transaction (e.g. ``"deposit"``, ``"bet"``, ``"withdrawal"``).

        Returns
        -------
        FraudCheckResult
            Outcome indicating whether the amount looks anomalous.
        """
        flags: list[str] = []
        risk_level = RiskLevel.LOW

        with self._lock:
            key = (player_id, action_type)
            history = self._amount_history[key]
            mean = history.mean()

            if (
                mean is not None
                and len(history.samples) >= _MIN_HISTORY_SAMPLES
                and amount > mean * self._anomaly_multiplier
            ):
                flags.append(
                    f"amount_anomaly:{action_type}:"
                    f"amount={amount},mean={mean:.2f}"
                )
                risk_level = RiskLevel.HIGH

            # Record this amount into history after the check
            history.add(amount)

        allowed = not (
            self._block_on_critical and risk_level == RiskLevel.CRITICAL
        )
        return FraudCheckResult(
            allowed=allowed,
            flags=flags,
            risk_level=risk_level,
        )

    def flag_suspicious(
        self,
        player_id: str,
        reason: str,
        severity: RiskLevel,
    ) -> None:
        """Manually attach a fraud flag to *player_id*.

        Used by compliance staff, automated rules engines, or other services
        to annotate a player outside of the normal velocity / amount checks.

        Parameters
        ----------
        player_id:
            The player to flag.
        reason:
            Human-readable explanation (logged, not shown to the player).
        severity:
            The risk level this flag should contribute.
        """
        with self._lock:
            self._manual_flags[player_id].append(
                _ManualFlag(
                    reason=reason,
                    severity=severity,
                    flagged_at=datetime.now(tz=timezone.utc),
                )
            )

    def get_risk_score(self, player_id: str) -> RiskScore:
        """Compute and return an aggregated risk assessment for *player_id*.

        The numeric score is derived from:
        * Active velocity-window breach counts (weighted by rule level).
        * Manual flag severities.

        Parameters
        ----------
        player_id:
            The player to assess.

        Returns
        -------
        RiskScore
            Aggregated risk assessment.
        """
        now = datetime.now(tz=timezone.utc)
        active_flag_codes: list[str] = []
        level_sum = 0.0

        with self._lock:
            # Tally velocity breaches
            for (pid, action_type), window in self._velocity.items():
                if pid != player_id:
                    continue
                relevant_rules = [r for r in self._rules if r.action_type == action_type]
                for rule in relevant_rules:
                    cutoff = now - timedelta(seconds=rule.window_seconds)
                    count = sum(1 for ts in window.events if ts >= cutoff)
                    if count > rule.max_count:
                        flag_code = (
                            f"velocity:{action_type}:{rule.max_count}/"
                            f"{rule.window_seconds}s"
                        )
                        active_flag_codes.append(flag_code)
                        level_sum += self._level_weight(rule.risk_level)

            # Tally manual flags
            manual_flag_codes = [
                f.reason
                for f in self._manual_flags.get(player_id, [])
            ]
            for flag in self._manual_flags.get(player_id, []):
                level_sum += self._level_weight(flag.severity)

        # Normalise score to [0.0, 1.0]
        score = min(1.0, level_sum / 4.0)
        level = self._score_to_level(score)

        return RiskScore(
            player_id=player_id,
            score=score,
            level=level,
            active_flags=active_flag_codes,
            manual_flags=manual_flag_codes,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _level_order(level: RiskLevel) -> int:
        """Return an integer ordering for risk levels (higher = more severe)."""
        return {
            RiskLevel.LOW: 0,
            RiskLevel.MEDIUM: 1,
            RiskLevel.HIGH: 2,
            RiskLevel.CRITICAL: 3,
        }[level]

    @staticmethod
    def _level_weight(level: RiskLevel) -> float:
        """Return a numeric weight for aggregating risk contributions."""
        return {
            RiskLevel.LOW: 0.1,
            RiskLevel.MEDIUM: 0.5,
            RiskLevel.HIGH: 1.0,
            RiskLevel.CRITICAL: 2.0,
        }[level]

    @staticmethod
    def _score_to_level(score: float) -> RiskLevel:
        """Map a normalised score to a categorical risk level."""
        if score >= 0.75:
            return RiskLevel.CRITICAL
        if score >= 0.50:
            return RiskLevel.HIGH
        if score >= 0.25:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

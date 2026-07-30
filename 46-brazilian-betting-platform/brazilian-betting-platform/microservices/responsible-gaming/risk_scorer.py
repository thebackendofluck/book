# Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Responsible Gaming Service — Behavioral Risk Scorer
====================================================
Computes a real-time behavioral risk score (0.0 – 1.0) for each player
based on multiple signals derived from their gaming activity.

Scoring model:
  Each signal contributes a weighted component.  The final score is the
  weighted average, clamped to [0, 1].

Signals:
  deposit_velocity    — deposits in last 24 h vs. baseline
  loss_chasing        — rapid bets after a significant loss streak
  session_duration    — session length relative to configured limit
  night_play_ratio    — fraction of sessions between 00:00–06:00 BRT
  limit_change_freq   — how often the player has changed their limits
  self_exclusion_hist — prior self-exclusion attempts
  alert_density       — number of alerts in the last 7 days

Risk levels:
  0.00 – 0.30  LOW
  0.31 – 0.60  MEDIUM
  0.61 – 0.80  HIGH
  0.81 – 1.00  CRITICAL

Production note: replace stub signal fetchers with real queries against
the transaction history, session log, and alert tables.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import structlog

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Risk level thresholds
# ---------------------------------------------------------------------------

THRESHOLDS: Dict[str, Tuple[float, float]] = {
    "low":      (0.00, 0.30),
    "medium":   (0.31, 0.60),
    "high":     (0.61, 0.80),
    "critical": (0.81, 1.00),
}


def score_to_level(score: float) -> str:
    for level, (lo, hi) in THRESHOLDS.items():
        if lo <= score <= hi:
            return level
    return "critical"


# ---------------------------------------------------------------------------
# Signal weights
# ---------------------------------------------------------------------------

SIGNAL_WEIGHTS: Dict[str, float] = {
    "deposit_velocity":     0.20,
    "loss_chasing":         0.25,
    "session_duration":     0.15,
    "night_play_ratio":     0.10,
    "limit_change_freq":    0.10,
    "self_exclusion_hist":  0.10,
    "alert_density":        0.10,
}


# ---------------------------------------------------------------------------
# Signal dataclass
# ---------------------------------------------------------------------------


@dataclass
class PlayerSignals:
    """Raw behavioral signals for a player, fetched from various sources."""

    cpf_hash: str
    deposits_last_24h: int = 0
    total_deposit_amount_24h: float = 0.0
    loss_streak_current: int = 0
    bets_after_loss: int = 0
    session_minutes_today: float = 0.0
    configured_session_limit_minutes: Optional[float] = None
    night_sessions_last_30d: int = 0
    total_sessions_last_30d: int = 0
    limit_changes_last_30d: int = 0
    prior_self_exclusions: int = 0
    alerts_last_7d: int = 0
    metadata: Dict[str, float] = field(default_factory=dict)


@dataclass
class RiskScoreResult:
    """Full risk assessment for a player."""

    cpf_hash: str
    overall_score: float
    risk_level: str
    components: Dict[str, float]
    signals: List[str]
    computed_at: datetime


# ---------------------------------------------------------------------------
# Individual signal scorers
# ---------------------------------------------------------------------------


def _score_deposit_velocity(signals: PlayerSignals) -> Tuple[float, Optional[str]]:
    """Rapid deposits in the last 24 h are a strong risk indicator."""
    if signals.deposits_last_24h >= 10:
        return 1.0, "10+ deposits in 24 h"
    if signals.deposits_last_24h >= 5:
        return 0.70, "5+ deposits in 24 h"
    if signals.deposits_last_24h >= 3:
        return 0.40, "3+ deposits in 24 h"
    return 0.10, None


def _score_loss_chasing(signals: PlayerSignals) -> Tuple[float, Optional[str]]:
    """Placing bets immediately after a loss streak (chasing losses)."""
    if signals.loss_streak_current >= 5 and signals.bets_after_loss >= 3:
        return 1.0, "Loss chasing: 5+ streak + 3+ bets"
    if signals.loss_streak_current >= 3 and signals.bets_after_loss >= 2:
        return 0.65, "Loss chasing: 3+ streak"
    if signals.loss_streak_current >= 2:
        return 0.30, "Minor loss streak"
    return 0.05, None


def _score_session_duration(signals: PlayerSignals) -> Tuple[float, Optional[str]]:
    """Sessions significantly exceeding the player's own configured limit."""
    limit = signals.configured_session_limit_minutes
    if limit is None or limit <= 0:
        # No limit set — moderate default concern
        if signals.session_minutes_today >= 180:
            return 0.50, "3+ hours session, no limit configured"
        return 0.10, None

    ratio = signals.session_minutes_today / limit
    if ratio >= 2.0:
        return 0.90, f"Session {ratio:.1f}× over limit"
    if ratio >= 1.5:
        return 0.60, f"Session {ratio:.1f}× over limit"
    if ratio >= 1.0:
        return 0.30, "Session at limit"
    return 0.05, None


def _score_night_play(signals: PlayerSignals) -> Tuple[float, Optional[str]]:
    """High night-time (00:00–06:00) play ratio suggests disordered gambling."""
    total = max(signals.total_sessions_last_30d, 1)
    ratio = signals.night_sessions_last_30d / total
    if ratio >= 0.50:
        return 0.85, f"{ratio:.0%} night-time play"
    if ratio >= 0.30:
        return 0.50, f"{ratio:.0%} night-time play"
    if ratio >= 0.15:
        return 0.25, f"{ratio:.0%} night-time play"
    return 0.05, None


def _score_limit_changes(signals: PlayerSignals) -> Tuple[float, Optional[str]]:
    """Frequent upward limit changes correlate with problem gambling."""
    if signals.limit_changes_last_30d >= 5:
        return 0.80, "5+ limit changes in 30 days"
    if signals.limit_changes_last_30d >= 3:
        return 0.50, "3+ limit changes in 30 days"
    return 0.05, None


def _score_self_exclusion_history(signals: PlayerSignals) -> Tuple[float, Optional[str]]:
    """Prior self-exclusions are one of the strongest predictors."""
    if signals.prior_self_exclusions >= 2:
        return 0.90, "Multiple prior self-exclusions"
    if signals.prior_self_exclusions == 1:
        return 0.60, "Prior self-exclusion on record"
    return 0.00, None


def _score_alert_density(signals: PlayerSignals) -> Tuple[float, Optional[str]]:
    """High alert volume in recent days."""
    if signals.alerts_last_7d >= 10:
        return 0.90, "10+ alerts in 7 days"
    if signals.alerts_last_7d >= 5:
        return 0.60, "5+ alerts in 7 days"
    if signals.alerts_last_7d >= 2:
        return 0.30, "Multiple alerts this week"
    return 0.05, None


# ---------------------------------------------------------------------------
# Risk Scorer
# ---------------------------------------------------------------------------


class RiskScorer:
    """
    Computes a player's overall behavioral risk score from raw signals.

    Usage:
        scorer = RiskScorer()
        signals = await scorer.fetch_signals(cpf_hash)
        result = scorer.compute(signals)
    """

    async def fetch_signals(self, cpf_hash: str) -> PlayerSignals:
        """
        Fetch behavioral signals for a player.

        Production: query transaction DB, session log, alert table.
        Stub: returns zeroed signals (safe for unit tests).
        """
        await asyncio.sleep(0.01)
        return PlayerSignals(cpf_hash=cpf_hash)

    def compute(self, signals: PlayerSignals) -> RiskScoreResult:
        """Compute and return the risk score from pre-fetched signals."""
        scorers = [
            ("deposit_velocity",    _score_deposit_velocity),
            ("loss_chasing",        _score_loss_chasing),
            ("session_duration",    _score_session_duration),
            ("night_play_ratio",    _score_night_play),
            ("limit_change_freq",   _score_limit_changes),
            ("self_exclusion_hist", _score_self_exclusion_history),
            ("alert_density",       _score_alert_density),
        ]

        components: Dict[str, float] = {}
        triggered_signals: List[str] = []

        for name, scorer_fn in scorers:
            score, signal_msg = scorer_fn(signals)
            components[name] = score
            if signal_msg:
                triggered_signals.append(signal_msg)

        # Weighted average
        total_weight = sum(SIGNAL_WEIGHTS.values())
        weighted_sum = sum(
            components[name] * SIGNAL_WEIGHTS.get(name, 0.0)
            for name in components
        )
        overall = min(1.0, max(0.0, weighted_sum / total_weight))

        risk_level = score_to_level(overall)
        now = datetime.now(timezone.utc)

        logger.info(
            "risk_score_computed",
            cpf_hash=signals.cpf_hash[:8],
            overall_score=round(overall, 3),
            risk_level=risk_level,
            signals_triggered=len(triggered_signals),
        )

        return RiskScoreResult(
            cpf_hash=signals.cpf_hash,
            overall_score=round(overall, 4),
            risk_level=risk_level,
            components=components,
            signals=triggered_signals,
            computed_at=now,
        )

    async def score(self, cpf_hash: str) -> RiskScoreResult:
        """Convenience method: fetch signals then compute score."""
        signals = await self.fetch_signals(cpf_hash)
        return self.compute(signals)

# Companion code for "The Backend of Luck" - Chapter 19, Anti-Fraud System Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
player_profiler.py – Build and maintain per-player risk profiles.

A PlayerRiskProfile aggregates the player's current:
  - Total score per risk matrix (rg, cir, cra, vip, aff)
  - Current matrix level
  - Active score-type IDs
  - 30-day deposit/decline counters
  - 90-day timeout count
  - Risk flags (e.g. "aml_watch", "self_excluded_pending")

Profiles are kept in an in-memory dict (Redis in production) and
refreshed after every scored event.  The HTTP API exposes GET/PUT
endpoints for back-office review.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from models import (
    MatrixLevel,
    MatrixScoreType,
    PlayerRiskProfile,
    UserMatrixLevel,
    UserMatrixScore,
)

import structlog
log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# In-memory profile store (replace with Redis/Postgres in production)
# ---------------------------------------------------------------------------


class ProfileStore:
    """Thread-safe in-memory store for PlayerRiskProfile objects."""

    def __init__(self) -> None:
        self._profiles: Dict[int, PlayerRiskProfile] = {}

    def get(self, user_id: int) -> Optional[PlayerRiskProfile]:
        return self._profiles.get(user_id)

    def set(self, profile: PlayerRiskProfile) -> None:
        self._profiles[profile.user_id] = profile

    def all_profiles(self) -> List[PlayerRiskProfile]:
        return list(self._profiles.values())

    def delete(self, user_id: int) -> bool:
        return bool(self._profiles.pop(user_id, None))


# ---------------------------------------------------------------------------
# Level resolution helpers
# ---------------------------------------------------------------------------


def resolve_level(score: int, levels: List[MatrixLevel]) -> Optional[MatrixLevel]:
    """
    Find the MatrixLevel whose score range contains *score*.
    Returns None if the score is below all defined level thresholds.
    """
    for level in sorted(levels, key=lambda l: l.min_score, reverse=True):
        if level.min_score <= score:
            return level
    return None


def compute_level_number(score: int, levels: List[MatrixLevel]) -> int:
    """Return the level_number (1-based) for a given score, or 0 if below all levels."""
    level = resolve_level(score, levels)
    return level.level_number if level else 0


# ---------------------------------------------------------------------------
# Profile builder
# ---------------------------------------------------------------------------


class PlayerProfiler:
    """
    Builds and updates a PlayerRiskProfile from raw scoring artefacts.

    Typical call flow:
      1. score_event() produces a list of matched matrix IDs + UserMatrixScore entries.
      2. PlayerProfiler.update_profile() is called to refresh the cached profile.
      3. The profile is read by the HTTP API for back-office display.
    """

    def __init__(
        self,
        profile_store: ProfileStore,
        matrix_levels: Dict[str, List[MatrixLevel]],  # matrix_id -> levels
    ) -> None:
        self._store = profile_store
        self._levels = matrix_levels  # static configuration

    def get_or_create(self, user_id: int, jurisdiction: str) -> PlayerRiskProfile:
        profile = self._store.get(user_id)
        if profile is None:
            profile = PlayerRiskProfile(user_id=user_id, jurisdiction=jurisdiction)
            self._store.set(profile)
        return profile

    def update_profile(
        self,
        user_id: int,
        jurisdiction: str,
        new_scores: List[UserMatrixScore],
        all_active_scores: Optional[List[UserMatrixScore]] = None,
    ) -> PlayerRiskProfile:
        """
        Rebuild the player's profile after a scoring run.

        Parameters
        ----------
        user_id / jurisdiction:
            Player identity.
        new_scores:
            Score entries just added in this scoring cycle.
        all_active_scores:
            Full list of active (non-disabled) scores for this user across all
            matrices.  If None, only incremental updates are made.
        """
        profile = self.get_or_create(user_id, jurisdiction)

        # Aggregate scores by matrix
        score_by_matrix: Dict[str, int] = dict(profile.scores)
        active_type_ids: List[int] = list(profile.active_score_types)

        for score in new_scores:
            matrix_id = _matrix_id_for_score(score, all_active_scores)
            if matrix_id:
                score_by_matrix[matrix_id] = score_by_matrix.get(matrix_id, 0) + 1
            if score.score_type_id not in active_type_ids:
                active_type_ids.append(score.score_type_id)

        # Resolve levels
        level_by_matrix: Dict[str, int] = {}
        for matrix_id, total_score in score_by_matrix.items():
            levels = self._levels.get(matrix_id, [])
            level_by_matrix[matrix_id] = compute_level_number(total_score, levels)

        updated = profile.model_copy(update={
            "scores": score_by_matrix,
            "levels": level_by_matrix,
            "active_score_types": active_type_ids,
            "last_calculated": datetime.now(timezone.utc),
        })
        self._store.set(updated)
        log.info(
            "Profile updated: user=%s matrices=%s levels=%s",
            user_id, score_by_matrix, level_by_matrix,
        )
        return updated

    def apply_deposit_metrics(
        self,
        user_id: int,
        deposit_total_cents: int,
        deposit_count: int,
        declined_count: int,
    ) -> PlayerRiskProfile:
        """
        Update 30-day deposit counters on the profile.
        Called after deposit-confirmed or deposit-declined events.
        """
        profile = self._store.get(user_id)
        if profile is None:
            return profile  # type: ignore

        updated = profile.model_copy(update={
            "total_deposits_30d": deposit_total_cents,
            "deposit_count_30d": deposit_count,
            "declined_deposits_30d": declined_count,
            "last_calculated": datetime.now(timezone.utc),
        })
        self._store.set(updated)
        return updated

    def apply_timeout_count(self, user_id: int, timeout_count_90d: int) -> PlayerRiskProfile:
        """Update 90-day timeout counter (used for RG2/RG3 scoring)."""
        profile = self._store.get(user_id)
        if profile is None:
            return profile  # type: ignore
        updated = profile.model_copy(update={
            "timeout_count_90d": timeout_count_90d,
            "last_calculated": datetime.now(timezone.utc),
        })
        self._store.set(updated)
        return updated

    def add_risk_flag(self, user_id: int, flag: str) -> Optional[PlayerRiskProfile]:
        """Add a named risk flag (e.g. 'aml_watch') to the player's profile."""
        profile = self._store.get(user_id)
        if profile is None:
            log.warning("add_risk_flag: user %s not found", user_id)
            return None
        flags = list(profile.risk_flags)
        if flag not in flags:
            flags.append(flag)
        updated = profile.model_copy(update={"risk_flags": flags})
        self._store.set(updated)
        return updated

    def remove_risk_flag(self, user_id: int, flag: str) -> Optional[PlayerRiskProfile]:
        """Remove a named risk flag from the player's profile."""
        profile = self._store.get(user_id)
        if profile is None:
            return None
        flags = [f for f in profile.risk_flags if f != flag]
        updated = profile.model_copy(update={"risk_flags": flags})
        self._store.set(updated)
        return updated

    def reset_matrix_score(self, user_id: int, matrix_id: str) -> Optional[PlayerRiskProfile]:
        """
        Reset scores and level for a specific matrix.
        Called when a reset-score event is processed.
        """
        profile = self._store.get(user_id)
        if profile is None:
            return None
        scores = dict(profile.scores)
        levels = dict(profile.levels)
        scores.pop(matrix_id, None)
        levels.pop(matrix_id, None)
        updated = profile.model_copy(update={
            "scores": scores,
            "levels": levels,
            "last_calculated": datetime.now(timezone.utc),
        })
        self._store.set(updated)
        return updated


# ---------------------------------------------------------------------------
# Risk thresholds for quick summary classification
# ---------------------------------------------------------------------------

RISK_CLASSIFICATION = {
    "LOW":    {"max_score": 7},
    "MEDIUM": {"min_score": 8, "max_score": 19},
    "HIGH":   {"min_score": 20, "max_score": 39},
    "SEVERE": {"min_score": 40},
}


def classify_overall_risk(profile: PlayerRiskProfile) -> str:
    """
    Derive an overall risk classification from the player's matrix scores.

    Uses the maximum score across all matrices as the primary signal.
    """
    if not profile.scores:
        return "LOW"
    max_score = max(profile.scores.values())
    if max_score >= RISK_CLASSIFICATION["SEVERE"]["min_score"]:
        return "SEVERE"
    if max_score >= RISK_CLASSIFICATION["HIGH"]["min_score"]:
        return "HIGH"
    if max_score >= RISK_CLASSIFICATION["MEDIUM"]["min_score"]:
        return "MEDIUM"
    return "LOW"


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _matrix_id_for_score(
    score: UserMatrixScore,
    all_active: Optional[List[UserMatrixScore]],
) -> Optional[str]:
    """
    Attempt to resolve the matrix_id for a UserMatrixScore.

    When all_active is provided it includes the score_matrix_id field set by
    the scorer.  For bare UserMatrixScore entries (from incremental updates)
    we return None and the caller handles aggregation elsewhere.
    """
    if hasattr(score, "score_matrix_id") and score.score_matrix_id:
        return score.score_matrix_id
    return None

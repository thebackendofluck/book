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
VIP tier matching algorithm.
Chapter 37 — Marketing Technology and CRM

Python equivalent of RuleProcessor.scala and ConditionLogic.scala.

Performs a linear scan over priority-ordered rules, returning the first
match. Each VipRule defines a rectangular region in two-dimensional space
(deposit volume x bet volume) with optional upper and lower boundaries
and an optional weighted score threshold.

With 12 rules per brand, the linear scan completes in microseconds.
Rule ordering in the database controls priority (first match wins).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass
class VipRule:
    """
    A single VIP tier rule.

    Boundaries are optional: None = no limit on that side.
    Example: deposit_low=50000, deposit_hi=None means "deposits >= EUR 500".
    """

    rule_id: int
    brand_id: int
    status_name: str
    tier: int
    deposit_low: int | None = None
    deposit_hi: int | None = None
    handle_low: int | None = None
    handle_hi: int | None = None
    min_weighted_score: Decimal | None = None


class ConditionLogic:
    """Boundary condition logic supporting open-ended ranges."""

    @staticmethod
    def applies_condition(
        value: int,
        low_bound: int | None,
        hi_bound: int | None,
    ) -> bool:
        """
        Check whether *value* falls within the half-open or closed range
        defined by *low_bound* and *hi_bound*.

        Matches the Scala pattern-match exactly:
          (Some(low), None)      => low <= value          (open upper bound)
          (Some(low), Some(hi))  => low <= value <= hi    (closed range)
          (None,      Some(hi))  => value <= hi           (open lower bound)
          (None,      None)      => False                 (never matches)
        """
        if low_bound is not None and hi_bound is None:
            return low_bound <= value
        if low_bound is not None and hi_bound is not None:
            return low_bound <= value <= hi_bound
        if low_bound is None and hi_bound is not None:
            return value <= hi_bound
        return False  # (None, None) — never matches


class RuleProcessor:
    """VIP tier matching: first-match linear scan over priority-ordered rules."""

    @staticmethod
    def apply(
        rules: list[VipRule],
        user_deposits_volume: int,
        user_bet_volume: int,
        weighted_score: Decimal | None = None,
    ) -> VipRule | None:
        """
        Return the first rule in *rules* whose deposit and bet boundaries
        match the given volumes, and whose optional weighted-score threshold
        is satisfied.

        Parameters
        ----------
        rules:
            Priority-ordered list of VipRule objects (database ORDER BY priority).
        user_deposits_volume:
            Player's 30-day cumulative deposit volume in cents.
        user_bet_volume:
            Player's 30-day cumulative bet volume in cents.
        weighted_score:
            Optional pre-computed weighted score from WeightedScoring.compute_score().
            If None, rules with a min_weighted_score threshold are skipped.

        Returns
        -------
        The first matching VipRule, or None if no rule matches.
        """
        for rule in rules:
            deposit_match = ConditionLogic.applies_condition(
                user_deposits_volume, rule.deposit_low, rule.deposit_hi
            )
            bet_match = ConditionLogic.applies_condition(
                user_bet_volume, rule.handle_low, rule.handle_hi
            )
            score_match = (
                rule.min_weighted_score is None
                or (
                    weighted_score is not None
                    and weighted_score >= rule.min_weighted_score
                )
            )
            if deposit_match and bet_match and score_match:
                return rule
        return None

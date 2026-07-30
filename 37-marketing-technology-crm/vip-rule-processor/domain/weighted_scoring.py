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
Multi-factor VIP scoring algorithm.
Chapter 37 — Marketing Technology and CRM

Python equivalent of WeightedScoring.scala.

Computes a weighted score combining net deposit value, bet volume (with
game-type multipliers), activity frequency, and account age. The score
feeds into tier matching as a third dimension alongside raw volumes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum


class GameType(str, Enum):
    LIVE_CASINO = "live_casino"
    TABLE_GAMES = "table_games"
    SLOTS = "slots"
    SPORTS_BET = "sports_bet"
    POKER = "poker"
    VIRTUAL = "virtual"


@dataclass
class ScoringFactors:
    """Input data for the weighted VIP scoring algorithm."""

    net_deposits: int                              # deposits minus withdrawals (cents)
    gross_bet_volume: int                          # total bets placed (cents)
    game_type_weights: dict[GameType, Decimal]     # per-game-type multipliers
    activity_days: int                             # distinct active days in window
    window_days: int                               # scoring window (default 30)
    account_age_days: int                          # total account age in days


class WeightedScoring:
    """
    Multi-factor VIP scoring.

    Score components:
      1. Net deposit score    — penalises deposit-then-withdraw patterns (weight 0.4)
      2. Weighted bet score   — applies game-type multipliers (weight 0.4)
      3. Frequency component  — active-day ratio bonus applied to net deposits (weight 0.2)
      4. Age factor           — loyalty bump for accounts >= 90 days (applied to total)

    Game type multipliers: live casino and table games carry higher weight
    because they indicate engaged, high-value play patterns.
    """

    DEFAULT_GAME_WEIGHTS: dict[GameType, Decimal] = {
        GameType.LIVE_CASINO: Decimal("1.5"),
        GameType.TABLE_GAMES: Decimal("1.2"),
        GameType.SLOTS: Decimal("1.0"),
        GameType.SPORTS_BET: Decimal("1.1"),
        GameType.POKER: Decimal("1.3"),
        GameType.VIRTUAL: Decimal("0.8"),
    }

    @classmethod
    def compute_score(cls, factors: ScoringFactors) -> Decimal:
        """
        Compute the weighted VIP score for a player.

        Matches the Scala implementation formula exactly.
        """
        # 1. Net deposit score: penalises deposit-then-withdraw patterns
        net_deposit_score = Decimal(factors.net_deposits) / 100

        # 2. Weighted bet volume: apply the maximum game-type multiplier
        if factors.game_type_weights:
            weighted_bet_score = max(
                Decimal(factors.gross_bet_volume) / 100 * weight
                for weight in factors.game_type_weights.values()
            )
        else:
            weighted_bet_score = Decimal(factors.gross_bet_volume) / 100

        # 3. Activity frequency bonus: daily players get up to 1.1x
        activity_ratio = factors.activity_days / factors.window_days
        frequency_bonus = Decimal(str(1.0 + activity_ratio * 0.1))

        # 4. Account age factor: accounts older than 90 days get a loyalty bump
        if factors.account_age_days >= 365:
            age_factor = Decimal("1.15")   # 1+ year loyalty
        elif factors.account_age_days >= 180:
            age_factor = Decimal("1.10")   # 6+ months
        elif factors.account_age_days >= 90:
            age_factor = Decimal("1.05")   # 3+ months
        else:
            age_factor = Decimal("1.00")   # new account, no bump

        # Combined weighted score
        combined = (
            net_deposit_score * Decimal("0.4")
            + weighted_bet_score * Decimal("0.4")
            + net_deposit_score * frequency_bonus * Decimal("0.2")
        ) * age_factor

        return combined

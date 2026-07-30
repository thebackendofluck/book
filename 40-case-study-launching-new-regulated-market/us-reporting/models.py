# Companion code for "The Backend of Luck" - Chapter 40, Case Study.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# US Reporting — Domain Models
# Source: Production casino platform (sanitized)
# Chapter 40 - Case Study
# =============================================================================

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional


@dataclass
class WsrData:
    """Wagering Summary Report row — one row per supplier / game type."""
    game_provider: str
    game_type_id: str
    product_desc: str
    game_type: str
    bonus_wager: Decimal
    cash_wager: Decimal
    resettled_bets: Decimal
    voided_bets: Decimal
    total_wager: Decimal
    cash_win_amount: Decimal
    bonus_win_amount: Decimal
    win_amount: Decimal
    win_or_loss_amount: Decimal


@dataclass
class StuckBetRow:
    """A wager that has not settled within the expected window."""
    supplier: str
    bet_id: str
    player_id: int
    amount: Decimal
    currency: str
    placed_at: str
    expected_settlement: str


class GameType:
    TABLE  = "Table"
    SPORTS = "Sports"
    SLOT   = "Slot"


@dataclass
class FailedStep:
    step_name: str
    details: str
    exception: Optional[Exception] = None

# Companion code for "The Backend of Luck" - Chapter 34, Data and Analytics.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
models.py — Domain models for the account history service.

Mirrors the Scala domain types from the account-history-service, reinterpreted
for egambling player account history tracking:
  - AccountEvent: base event type (transaction, session, game round, balance change)
  - TransactionHistory: deposits, withdrawals, bonuses
  - SessionHistory: login/logout sessions with duration
  - GameRoundHistory: individual game rounds (bet, win, GGR)
  - PlayerStats: aggregated stats per player (deposits, withdrawals, GGR)

The original Scala service tracked venue reservations. This Python version
extends the pattern to a gambling-specific account history context,
consistent with the chapter's regulatory and RG obligations
(transaction history retention ≥ 5 years, session/play-time disclosure).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class EventType(str, Enum):
    DEPOSIT          = "deposit"
    WITHDRAWAL       = "withdrawal"
    BET              = "bet"
    WIN              = "win"
    BONUS_AWARD      = "bonus_award"
    BONUS_WAGERED    = "bonus_wagered"
    SESSION_START    = "session_start"
    SESSION_END      = "session_end"
    BALANCE_CHANGE   = "balance_change"
    GAME_ROUND_START = "game_round_start"
    GAME_ROUND_END   = "game_round_end"


class TransactionStatus(str, Enum):
    PENDING   = "pending"
    COMPLETED = "completed"
    FAILED    = "failed"
    REVERSED  = "reversed"


class GameOutcome(str, Enum):
    WIN  = "win"
    LOSS = "loss"
    VOID = "void"
    PUSH = "push"


# ---------------------------------------------------------------------------
# Core event model
# ---------------------------------------------------------------------------

@dataclass
class AccountEvent:
    """
    Base event record — every action on a player account is captured as an event.

    Immutable after creation (event sourcing pattern): events are never
    updated or deleted; corrections are expressed as new events.
    """
    id:          int
    player_id:   int
    event_type:  EventType
    amount:      float          # In minor currency units (cents / pence)
    currency:    str            # ISO-4217, e.g. "GBP", "SEK", "EUR", "BRL"
    occurred_at: datetime
    reference:   Optional[str] = None    # External transaction / round ID
    metadata:    Optional[dict] = None   # Flexible key-value for event details


# ---------------------------------------------------------------------------
# Transaction history
# ---------------------------------------------------------------------------

@dataclass
class TransactionHistory:
    """
    Deposit, withdrawal, or bonus transaction record.

    Retained for ≥ 5 years per UK/SE/DK regulatory requirements.
    """
    id:                 int
    player_id:          int
    transaction_type:   str                  # "deposit" | "withdrawal" | "bonus"
    amount:             float
    currency:           str
    status:             TransactionStatus
    initiated_at:       datetime
    completed_at:       Optional[datetime]
    payment_method:     Optional[str] = None  # "card" | "bank_transfer" | "pix"
    external_ref:       Optional[str] = None  # PSP transaction ID


# ---------------------------------------------------------------------------
# Session history
# ---------------------------------------------------------------------------

@dataclass
class SessionHistory:
    """
    Player login session — used for play-time tracking and RG obligations.

    Operators must provide players with session duration history on request
    (UKGC SR code 3.4.1, Spelinspektionen ch.6).
    """
    id:              int
    player_id:       int
    session_token:   str
    started_at:      datetime
    ended_at:        Optional[datetime]
    ip_address:      Optional[str]
    device_type:     Optional[str]          # "desktop" | "mobile" | "tablet"
    jurisdiction:    Optional[str]          # "GB" | "SE" | "DK" | "BR"

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.ended_at and self.started_at:
            return (self.ended_at - self.started_at).total_seconds()
        return None


# ---------------------------------------------------------------------------
# Game round history
# ---------------------------------------------------------------------------

@dataclass
class GameRoundHistory:
    """
    Individual game round record (slots, live casino, sports bet settlement).

    GGR (Gross Gaming Revenue) = sum(bets) - sum(wins) per round.
    Operators must retain round data for ≥ 5 years (UKGC, Spelinspektionen).
    """
    id:              int
    player_id:       int
    session_id:      Optional[int]
    game_id:         str
    game_name:       Optional[str]
    bet_amount:      float
    win_amount:      float
    currency:        str
    outcome:         GameOutcome
    started_at:      datetime
    ended_at:        Optional[datetime]
    round_ref:       Optional[str] = None    # Game provider round ID

    @property
    def ggr(self) -> float:
        """Gross Gaming Revenue for this round (operator perspective)."""
        return self.bet_amount - self.win_amount


# ---------------------------------------------------------------------------
# Aggregated stats
# ---------------------------------------------------------------------------

@dataclass
class PlayerStats:
    """
    Aggregated financial statistics for a player over a given time window.

    Used for:
      - RG affordability checks
      - Bonus abuse detection
      - Regulatory reporting (HMRC, Spelinspektionen, Spillemyndigheden, SEAE)
    """
    player_id:        int
    from_date:        datetime
    to_date:          datetime
    total_deposits:   float
    total_withdrawals: float
    total_bets:       float
    total_wins:       float
    bonus_awarded:    float
    bonus_wagered:    float
    currency:         str
    session_count:    int = 0
    total_play_time_seconds: float = 0.0

    @property
    def net_deposits(self) -> float:
        return self.total_deposits - self.total_withdrawals

    @property
    def ggr(self) -> float:
        """Gross Gaming Revenue = bets - wins (operator perspective)."""
        return self.total_bets - self.total_wins

    @property
    def ngr(self) -> float:
        """Net Gaming Revenue = GGR - bonuses awarded."""
        return self.ggr - self.bonus_awarded


# ---------------------------------------------------------------------------
# Query filter / response wrappers
# ---------------------------------------------------------------------------

@dataclass
class HistoryFilter:
    """Filter parameters for history queries."""
    player_id:    int
    from_date:    Optional[datetime] = None
    to_date:      Optional[datetime] = None
    event_types:  Optional[list[str]] = None
    min_amount:   Optional[float] = None
    max_amount:   Optional[float] = None
    limit:        int = 100
    offset:       int = 0


@dataclass
class PaginatedResult:
    """Paginated query response."""
    items:      list
    total:      int
    limit:      int
    offset:     int
    has_more:   bool = field(init=False)

    def __post_init__(self) -> None:
        self.has_more = (self.offset + self.limit) < self.total

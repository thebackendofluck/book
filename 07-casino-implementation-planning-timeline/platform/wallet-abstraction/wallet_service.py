#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 07, Casino Implementation Planning and Timeline.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Wallet Abstraction Layer for Game Provider Integration

Provides a unified wallet interface that game providers connect to via
a standardized API. Handles the translation between different game provider
wallet protocols (seamless/transfer) and the internal player balance.

Features:
- Seamless wallet integration (real-time balance queries)
- Transfer wallet support (session-based fund transfers)
- Idempotent transaction processing (prevents duplicate bets/wins)
- Multi-currency support with exchange rate handling
- Round-based transaction grouping
- Rollback support for failed game rounds
- Complete audit trail for regulatory compliance

Usage:
    from wallet_service import WalletService
    wallet = WalletService()

    # Seamless wallet flow (provider queries balance, places bet, credits win)
    balance = wallet.get_balance("player-123", "EUR")
    result = wallet.debit("player-123", 5.00, "EUR", round_id="round-abc", game_id="sweet-bonanza")
    result = wallet.credit("player-123", 12.50, "EUR", round_id="round-abc", game_id="sweet-bonanza")

    # Transfer wallet flow (bulk transfer to provider session)
    wallet.transfer_to_provider("player-123", "evolution", 100.00, "EUR")
    wallet.transfer_from_provider("player-123", "evolution", 85.50, "EUR")

    # CLI demo
    python3 wallet_service.py --demo
"""

import json
import logging
import argparse
import uuid
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class WalletTransactionType(Enum):
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    BET = "bet"
    WIN = "win"
    REFUND = "refund"
    ROLLBACK = "rollback"
    BONUS_CREDIT = "bonus_credit"
    BONUS_FORFEIT = "bonus_forfeit"
    JACKPOT_WIN = "jackpot_win"
    TRANSFER_TO_PROVIDER = "transfer_to_provider"
    TRANSFER_FROM_PROVIDER = "transfer_from_provider"
    ADJUSTMENT = "adjustment"       # Manual admin adjustment


class WalletStatus(Enum):
    ACTIVE = "active"
    FROZEN = "frozen"              # AML/compliance freeze
    SELF_EXCLUDED = "self_excluded"
    CLOSED = "closed"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class WalletBalance:
    """Player wallet balance."""
    player_id: str
    currency: str
    real_balance: float            # Real money balance
    bonus_balance: float           # Bonus/promotional balance
    locked_balance: float          # Funds locked in active game sessions
    withdrawable_balance: float    # real_balance - wagering requirements
    total_balance: float           # real_balance + bonus_balance
    status: str
    last_updated: str


@dataclass
class WalletTransaction:
    """A single wallet transaction."""
    transaction_id: str
    player_id: str
    type: str
    amount: float
    currency: str
    balance_before: float
    balance_after: float
    game_id: Optional[str]
    round_id: Optional[str]
    provider_id: Optional[str]
    provider_txn_id: Optional[str]  # Provider's transaction reference
    idempotency_key: str
    created_at: str
    metadata: dict = field(default_factory=dict)


@dataclass
class GameRound:
    """Tracks a complete game round (bet + win/loss)."""
    round_id: str
    player_id: str
    game_id: str
    provider_id: str
    status: str                    # open, closed, rolled_back
    total_bet: float
    total_win: float
    net_result: float
    transactions: list
    opened_at: str
    closed_at: Optional[str]


# ---------------------------------------------------------------------------
# Wallet Service
# ---------------------------------------------------------------------------

class WalletService:
    """
    Core wallet service implementing both seamless and transfer wallet patterns.

    Thread-safe: Uses locks for balance modifications to prevent race conditions.
    In production, this would use database-level row locking (SELECT FOR UPDATE).
    """

    def __init__(self):
        # In production: PostgreSQL with row-level locking
        self._balances: dict = {}                # player_id -> WalletBalance
        self._transactions: list = []
        self._idempotency_cache: dict = {}       # idempotency_key -> WalletTransaction
        self._game_rounds: dict = {}             # round_id -> GameRound
        self._provider_sessions: dict = {}       # (player_id, provider_id) -> locked_amount
        self._locks: dict = defaultdict(threading.Lock)

    def create_wallet(self, player_id: str, currency: str = "EUR") -> WalletBalance:
        """Create a new wallet for a player."""
        if player_id in self._balances:
            return self._balances[player_id]

        balance = WalletBalance(
            player_id=player_id,
            currency=currency,
            real_balance=0.0,
            bonus_balance=0.0,
            locked_balance=0.0,
            withdrawable_balance=0.0,
            total_balance=0.0,
            status=WalletStatus.ACTIVE.value,
            last_updated=datetime.now(timezone.utc).isoformat(),
        )
        self._balances[player_id] = balance
        logger.info(f"Wallet created for {player_id} ({currency})")
        return balance

    def get_balance(self, player_id: str, currency: str = "EUR") -> WalletBalance:
        """
        Get the current balance for a player.

        This is called by game providers on EVERY spin/bet to display
        the current balance. Must be extremely fast (< 5ms target).
        """
        balance = self._balances.get(player_id)
        if not balance:
            raise ValueError(f"Wallet not found for player {player_id}")

        if balance.status != WalletStatus.ACTIVE.value:
            raise ValueError(f"Wallet is {balance.status} for player {player_id}")

        return balance

    def debit(
        self,
        player_id: str,
        amount: float,
        currency: str,
        round_id: str,
        game_id: str,
        provider_id: str = "default",
        provider_txn_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        is_bonus: bool = False,
    ) -> WalletTransaction:
        """
        Debit (bet) from player wallet.

        Called by game providers when a player places a bet.
        Must be idempotent - duplicate calls with same idempotency_key
        return the original result without deducting again.
        """
        idem_key = idempotency_key or f"debit:{player_id}:{round_id}:{provider_txn_id}"

        # Check idempotency
        if idem_key in self._idempotency_cache:
            logger.info(f"Idempotent debit returned (key: {idem_key})")
            return self._idempotency_cache[idem_key]

        with self._locks[player_id]:
            balance = self._balances.get(player_id)
            if not balance:
                raise ValueError(f"Wallet not found: {player_id}")

            if balance.status != WalletStatus.ACTIVE.value:
                raise ValueError(f"Wallet is {balance.status}")

            if amount <= 0:
                raise ValueError(f"Invalid debit amount: {amount}")

            # Check sufficient funds
            available = balance.bonus_balance if is_bonus else balance.real_balance
            if amount > available + 0.001:  # Small epsilon for floating point
                raise ValueError(
                    f"Insufficient funds: requested {amount:.2f}, "
                    f"available {available:.2f}"
                )

            # Deduct balance
            balance_before = balance.real_balance + balance.bonus_balance
            if is_bonus:
                balance.bonus_balance = round(balance.bonus_balance - amount, 2)
            else:
                balance.real_balance = round(balance.real_balance - amount, 2)

            balance.total_balance = round(balance.real_balance + balance.bonus_balance, 2)
            balance.withdrawable_balance = balance.real_balance  # Simplified
            balance.last_updated = datetime.now(timezone.utc).isoformat()

            # Create transaction record
            txn = WalletTransaction(
                transaction_id=f"txn-{uuid.uuid4().hex[:12]}",
                player_id=player_id,
                type=WalletTransactionType.BET.value,
                amount=-amount,  # Negative for debit
                currency=currency,
                balance_before=round(balance_before, 2),
                balance_after=balance.total_balance,
                game_id=game_id,
                round_id=round_id,
                provider_id=provider_id,
                provider_txn_id=provider_txn_id,
                idempotency_key=idem_key,
                created_at=datetime.now(timezone.utc).isoformat(),
            )

            self._transactions.append(txn)
            self._idempotency_cache[idem_key] = txn

            # Track game round
            self._track_round(round_id, player_id, game_id, provider_id, "bet", amount)

            logger.info(f"Debit: {player_id} | -{amount:.2f} {currency} | "
                        f"Balance: {balance.total_balance:.2f} | Round: {round_id}")

            return txn

    def credit(
        self,
        player_id: str,
        amount: float,
        currency: str,
        round_id: str,
        game_id: str,
        provider_id: str = "default",
        provider_txn_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        is_jackpot: bool = False,
    ) -> WalletTransaction:
        """
        Credit (win) to player wallet.

        Called by game providers when a player wins.
        Amount of 0.0 is valid (indicates a losing round closure).
        """
        idem_key = idempotency_key or f"credit:{player_id}:{round_id}:{provider_txn_id}"

        if idem_key in self._idempotency_cache:
            logger.info(f"Idempotent credit returned (key: {idem_key})")
            return self._idempotency_cache[idem_key]

        with self._locks[player_id]:
            balance = self._balances.get(player_id)
            if not balance:
                raise ValueError(f"Wallet not found: {player_id}")

            if amount < 0:
                raise ValueError(f"Invalid credit amount: {amount}")

            balance_before = balance.real_balance + balance.bonus_balance
            balance.real_balance = round(balance.real_balance + amount, 2)
            balance.total_balance = round(balance.real_balance + balance.bonus_balance, 2)
            balance.withdrawable_balance = balance.real_balance
            balance.last_updated = datetime.now(timezone.utc).isoformat()

            txn_type = (WalletTransactionType.JACKPOT_WIN.value if is_jackpot
                        else WalletTransactionType.WIN.value)

            txn = WalletTransaction(
                transaction_id=f"txn-{uuid.uuid4().hex[:12]}",
                player_id=player_id,
                type=txn_type,
                amount=amount,
                currency=currency,
                balance_before=round(balance_before, 2),
                balance_after=balance.total_balance,
                game_id=game_id,
                round_id=round_id,
                provider_id=provider_id,
                provider_txn_id=provider_txn_id,
                idempotency_key=idem_key,
                created_at=datetime.now(timezone.utc).isoformat(),
            )

            self._transactions.append(txn)
            self._idempotency_cache[idem_key] = txn

            self._track_round(round_id, player_id, game_id, provider_id, "win", amount)

            logger.info(f"Credit: {player_id} | +{amount:.2f} {currency} | "
                        f"Balance: {balance.total_balance:.2f} | Round: {round_id}")

            return txn

    def rollback(
        self,
        player_id: str,
        round_id: str,
        provider_id: str = "default",
        reason: str = "Game round cancelled",
    ) -> WalletTransaction:
        """
        Rollback a game round - refund all bets placed in the round.

        Called when a game round is cancelled (e.g., network error,
        provider error, or regulatory requirement).
        """
        game_round = self._game_rounds.get(round_id)
        if not game_round:
            raise ValueError(f"Game round not found: {round_id}")

        if game_round.status == "rolled_back":
            logger.warning(f"Round {round_id} already rolled back")
            return self._idempotency_cache.get(f"rollback:{round_id}")  # ty:ignore[invalid-return-type]

        with self._locks[player_id]:
            balance = self._balances.get(player_id)
            if not balance:
                raise ValueError(f"Wallet not found: {player_id}")

            # Calculate net amount to refund (total bet - total win already credited)
            refund_amount = game_round.total_bet - game_round.total_win

            balance_before = balance.total_balance
            balance.real_balance = round(balance.real_balance + refund_amount, 2)
            balance.total_balance = round(balance.real_balance + balance.bonus_balance, 2)
            balance.last_updated = datetime.now(timezone.utc).isoformat()

            txn = WalletTransaction(
                transaction_id=f"txn-{uuid.uuid4().hex[:12]}",
                player_id=player_id,
                type=WalletTransactionType.ROLLBACK.value,
                amount=refund_amount,
                currency=balance.currency,
                balance_before=round(balance_before, 2),
                balance_after=balance.total_balance,
                game_id=game_round.game_id,
                round_id=round_id,
                provider_id=provider_id,
                provider_txn_id=None,
                idempotency_key=f"rollback:{round_id}",
                created_at=datetime.now(timezone.utc).isoformat(),
                metadata={"reason": reason},
            )

            game_round.status = "rolled_back"
            game_round.closed_at = datetime.now(timezone.utc).isoformat()

            self._transactions.append(txn)
            self._idempotency_cache[f"rollback:{round_id}"] = txn

            logger.info(f"Rollback: {player_id} | +{refund_amount:.2f} | Round: {round_id}")

            return txn

    def transfer_to_provider(
        self,
        player_id: str,
        provider_id: str,
        amount: float,
        currency: str,
    ) -> WalletTransaction:
        """
        Transfer funds from player wallet to a game provider session.

        Used with transfer wallet (non-seamless) providers where the player's
        funds are moved to the provider's system for the duration of play.
        """
        with self._locks[player_id]:
            balance = self._balances.get(player_id)
            if not balance:
                raise ValueError(f"Wallet not found: {player_id}")

            if amount > balance.real_balance:
                raise ValueError(f"Insufficient funds for transfer")

            balance_before = balance.total_balance
            balance.real_balance = round(balance.real_balance - amount, 2)
            balance.locked_balance = round(balance.locked_balance + amount, 2)
            balance.total_balance = round(balance.real_balance + balance.bonus_balance, 2)
            balance.last_updated = datetime.now(timezone.utc).isoformat()

            session_key = (player_id, provider_id)
            current = self._provider_sessions.get(session_key, 0.0)
            self._provider_sessions[session_key] = current + amount

            txn = WalletTransaction(
                transaction_id=f"txn-{uuid.uuid4().hex[:12]}",
                player_id=player_id,
                type=WalletTransactionType.TRANSFER_TO_PROVIDER.value,
                amount=-amount,
                currency=currency,
                balance_before=round(balance_before, 2),
                balance_after=balance.total_balance,
                game_id=None,
                round_id=None,
                provider_id=provider_id,
                provider_txn_id=None,
                idempotency_key=f"transfer_to:{player_id}:{provider_id}:{uuid.uuid4().hex[:8]}",
                created_at=datetime.now(timezone.utc).isoformat(),
            )

            self._transactions.append(txn)
            logger.info(f"Transfer to {provider_id}: {player_id} | {amount:.2f} {currency}")

            return txn

    def transfer_from_provider(
        self,
        player_id: str,
        provider_id: str,
        amount: float,
        currency: str,
    ) -> WalletTransaction:
        """
        Transfer funds back from a game provider session to player wallet.
        """
        with self._locks[player_id]:
            balance = self._balances.get(player_id)
            if not balance:
                raise ValueError(f"Wallet not found: {player_id}")

            balance_before = balance.total_balance
            balance.real_balance = round(balance.real_balance + amount, 2)
            balance.locked_balance = round(max(0, balance.locked_balance - amount), 2)
            balance.total_balance = round(balance.real_balance + balance.bonus_balance, 2)
            balance.last_updated = datetime.now(timezone.utc).isoformat()

            session_key = (player_id, provider_id)
            self._provider_sessions.pop(session_key, None)

            txn = WalletTransaction(
                transaction_id=f"txn-{uuid.uuid4().hex[:12]}",
                player_id=player_id,
                type=WalletTransactionType.TRANSFER_FROM_PROVIDER.value,
                amount=amount,
                currency=currency,
                balance_before=round(balance_before, 2),
                balance_after=balance.total_balance,
                game_id=None,
                round_id=None,
                provider_id=provider_id,
                provider_txn_id=None,
                idempotency_key=f"transfer_from:{player_id}:{provider_id}:{uuid.uuid4().hex[:8]}",
                created_at=datetime.now(timezone.utc).isoformat(),
            )

            self._transactions.append(txn)
            logger.info(f"Transfer from {provider_id}: {player_id} | +{amount:.2f} {currency}")

            return txn

    def get_transaction_history(
        self,
        player_id: str,
        limit: int = 50,
        txn_type: Optional[str] = None,
    ) -> list:
        """Get transaction history for a player."""
        txns = [t for t in self._transactions if t.player_id == player_id]
        if txn_type:
            txns = [t for t in txns if t.type == txn_type]
        return txns[-limit:]

    def get_game_round(self, round_id: str) -> Optional[GameRound]:
        """Get a game round by ID."""
        return self._game_rounds.get(round_id)

    # -----------------------------------------------------------------------
    # Private methods
    # -----------------------------------------------------------------------

    def _track_round(
        self, round_id: str, player_id: str, game_id: str,
        provider_id: str, action: str, amount: float,
    ):
        """Track a game round for audit and reconciliation."""
        if round_id not in self._game_rounds:
            self._game_rounds[round_id] = GameRound(
                round_id=round_id,
                player_id=player_id,
                game_id=game_id,
                provider_id=provider_id,
                status="open",
                total_bet=0.0,
                total_win=0.0,
                net_result=0.0,
                transactions=[],
                opened_at=datetime.now(timezone.utc).isoformat(),
                closed_at=None,
            )

        game_round = self._game_rounds[round_id]

        if action == "bet":
            game_round.total_bet = round(game_round.total_bet + amount, 2)
        elif action == "win":
            game_round.total_win = round(game_round.total_win + amount, 2)
            # Close round on win (even 0.00 win closes the round)
            game_round.status = "closed"
            game_round.closed_at = datetime.now(timezone.utc).isoformat()

        game_round.net_result = round(game_round.total_win - game_round.total_bet, 2)
        game_round.transactions.append({
            "action": action,
            "amount": amount,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })


def run_demo():
    """Run a demonstration of the wallet service."""
    wallet = WalletService()

    print("\n" + "=" * 70)
    print("  WALLET ABSTRACTION LAYER DEMO")
    print("=" * 70)

    # Setup: Create wallet and add funds
    print("\n--- Setup: Create Wallet & Deposit ---")
    wallet.create_wallet("player-001", "EUR")
    # Simulate a deposit by directly setting balance (in production, via payment service)
    wallet._balances["player-001"].real_balance = 500.00
    wallet._balances["player-001"].total_balance = 500.00
    wallet._balances["player-001"].withdrawable_balance = 500.00
    bal = wallet.get_balance("player-001")
    print(f"  Player: player-001 | Balance: EUR {bal.total_balance:.2f}")

    # Demo 1: Seamless wallet - single game round
    print("\n--- Demo 1: Seamless Wallet - Slot Game Round ---")
    print("  Game: Sweet Bonanza | Provider: Pragmatic Play")

    bet_txn = wallet.debit(
        "player-001", 2.00, "EUR",
        round_id="round-001", game_id="sweet-bonanza",
        provider_id="pragmatic-play", provider_txn_id="pp-bet-001",
    )
    print(f"  Bet: EUR 2.00 | Balance: EUR {wallet.get_balance('player-001').total_balance:.2f}")

    win_txn = wallet.credit(
        "player-001", 8.50, "EUR",
        round_id="round-001", game_id="sweet-bonanza",
        provider_id="pragmatic-play", provider_txn_id="pp-win-001",
    )
    print(f"  Win: EUR 8.50 | Balance: EUR {wallet.get_balance('player-001').total_balance:.2f}")

    game_round = wallet.get_game_round("round-001")
    print(f"  Round result: Bet {game_round.total_bet:.2f}, Win {game_round.total_win:.2f}, "  # ty:ignore[possibly-missing-attribute]
          f"Net: {game_round.net_result:+.2f}")  # ty:ignore[possibly-missing-attribute]

    # Demo 2: Losing round
    print("\n--- Demo 2: Seamless Wallet - Losing Round ---")
    wallet.debit("player-001", 5.00, "EUR", round_id="round-002",
                 game_id="gates-of-olympus", provider_id="pragmatic-play",
                 provider_txn_id="pp-bet-002")
    wallet.credit("player-001", 0.00, "EUR", round_id="round-002",
                  game_id="gates-of-olympus", provider_id="pragmatic-play",
                  provider_txn_id="pp-win-002")
    print(f"  Round result: Loss EUR 5.00 | Balance: EUR {wallet.get_balance('player-001').total_balance:.2f}")

    # Demo 3: Idempotency - duplicate bet call
    print("\n--- Demo 3: Idempotency Protection ---")
    dup_txn = wallet.debit(
        "player-001", 2.00, "EUR",
        round_id="round-001", game_id="sweet-bonanza",
        provider_id="pragmatic-play", provider_txn_id="pp-bet-001",
    )
    print(f"  Duplicate bet call returned same txn: {dup_txn.transaction_id}")
    print(f"  Balance unchanged: EUR {wallet.get_balance('player-001').total_balance:.2f}")

    # Demo 4: Rollback
    print("\n--- Demo 4: Round Rollback ---")
    wallet.debit("player-001", 10.00, "EUR", round_id="round-003",
                 game_id="crazy-time", provider_id="evolution",
                 provider_txn_id="evo-bet-003")
    print(f"  Bet placed: EUR 10.00 | Balance: EUR {wallet.get_balance('player-001').total_balance:.2f}")

    rb_txn = wallet.rollback("player-001", "round-003", reason="Network error during game")
    print(f"  Rollback: +EUR {rb_txn.amount:.2f} | Balance: EUR {wallet.get_balance('player-001').total_balance:.2f}")

    # Demo 5: Transfer wallet
    print("\n--- Demo 5: Transfer Wallet (Non-Seamless Provider) ---")
    wallet.transfer_to_provider("player-001", "microgaming", 100.00, "EUR")
    print(f"  Transferred EUR 100 to Microgaming session")
    print(f"  Main balance: EUR {wallet.get_balance('player-001').total_balance:.2f}")
    print(f"  Locked: EUR {wallet.get_balance('player-001').locked_balance:.2f}")

    # Player finishes playing, provider returns remaining funds
    wallet.transfer_from_provider("player-001", "microgaming", 85.50, "EUR")
    print(f"  Returned EUR 85.50 from Microgaming (lost EUR 14.50)")
    print(f"  Main balance: EUR {wallet.get_balance('player-001').total_balance:.2f}")

    # Demo 6: Transaction history
    print("\n--- Demo 6: Transaction History ---")
    history = wallet.get_transaction_history("player-001")
    print(f"  Total transactions: {len(history)}")
    for txn in history:
        sign = "+" if txn.amount >= 0 else ""
        print(f"  {txn.transaction_id} | {txn.type:<25} | {sign}{txn.amount:.2f} EUR | "
              f"{txn.balance_after:.2f}")

    print("\n" + "=" * 70)


def main():
    parser = argparse.ArgumentParser(description="Wallet Service Demo")
    parser.add_argument("--demo", action="store_true", help="Run demo")
    args = parser.parse_args()
    run_demo()


if __name__ == "__main__":
    main()

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
Supplier Callback Replay — Game Round Integrity Proof Script

Purpose
-------
Demonstrate that the Game Aggregation Layer (GAL) maintains correct player
balances under duplicate supplier callbacks for bets, wins, and rollbacks.

This is the second line of defence (the GAL itself being the first). Even if
a supplier's system delivers the same callback twice due to a network retry,
timeout, or their own bug, the platform must never:
  * debit the player twice for the same bet
  * credit the player twice for the same win
  * apply a rollback twice

Scenarios covered
-----------------
1. Bet callback → verify debit applied once
2. SAME bet callback replayed → verify NO double-debit
3. Win callback → verify credit applied once
4. SAME win callback replayed → verify NO double-credit
5. Rollback callback → verify reversal applied once
6. SAME rollback replayed → verify idempotent

Run as a standalone script:
    cd chapter-10/platform-core/gameservice
    python supplier_callback_replay.py

Run with pytest:
    pytest supplier_callback_replay.py -v

All scenarios are self-contained (no external services required).
"""

from __future__ import annotations

import asyncio
import logging
import sys
import os
import uuid
from decimal import Decimal
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from transaction_result import (
    BalanceStatus,
    TransactionResult,
    TransactionStatus,
    TransactionType,
    already_processed_result,
    success_result,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# In-memory wallet with idempotency tracking
# ---------------------------------------------------------------------------


class GameWallet:
    """
    Minimal in-memory wallet that enforces round-level idempotency.

    Each (player_id, supplier_ref) pair is tracked.  On replay the original
    result is returned with TransactionStatus.ALREADY_PROCESSED so callers
    can detect the replay without raising an exception.

    In production this would be backed by a Postgres table with a UNIQUE
    constraint on (supplier_id, supplier_ref) and a SELECT-FOR-UPDATE lock.
    """

    def __init__(self, player_id: str, currency: str, initial_balance: Decimal) -> None:
        self.player_id = player_id
        self.currency = currency
        self._cash_balance: Decimal = initial_balance
        self._bonus_balance: Decimal = Decimal("0")

        # supplier_ref → TransactionResult (idempotency store)
        self._processed: dict[str, TransactionResult] = {}

        # supplier_ref → original debit amount (for rollback validation)
        self._debits: dict[str, Decimal] = {}

    # ------------------------------------------------------------------
    # Balance helpers
    # ------------------------------------------------------------------

    @property
    def balance(self) -> BalanceStatus:
        return BalanceStatus(
            cash_balance=self._cash_balance,
            bonus_balance=self._bonus_balance,
            currency=self.currency,
        )

    @property
    def cash(self) -> Decimal:
        return self._cash_balance

    # ------------------------------------------------------------------
    # Operations
    # ------------------------------------------------------------------

    def debit(self, supplier_ref: str, amount: Decimal) -> TransactionResult:
        """
        Debit (bet) the player's wallet.

        Returns ALREADY_PROCESSED if supplier_ref was seen before.
        """
        if supplier_ref in self._processed:
            existing = self._processed[supplier_ref]
            logger.info(
                "DEBIT replay blocked supplier_ref=%s existing_tx=%s",
                supplier_ref, existing.tx_id,
            )
            return already_processed_result(
                tx_type=TransactionType.DEBIT,
                tx_id=existing.tx_id,
                balance=self.balance,
            )

        if amount > self._cash_balance:
            raise ValueError(
                f"Insufficient funds: balance={self._cash_balance} requested={amount}"
            )

        self._cash_balance -= amount
        self._debits[supplier_ref] = amount
        result = success_result(
            tx_type=TransactionType.DEBIT,
            balance=self.balance,
            cash_usage=amount,
        )
        self._processed[supplier_ref] = result
        logger.info(
            "DEBIT applied supplier_ref=%s amount=%s new_balance=%s",
            supplier_ref, amount, self._cash_balance,
        )
        return result

    def credit(self, supplier_ref: str, bet_ref: str, amount: Decimal) -> TransactionResult:
        """
        Credit (win) the player's wallet.

        `bet_ref` is the original debit's supplier_ref (used to associate the
        win to the bet for reporting). Returns ALREADY_PROCESSED on replay.
        """
        if supplier_ref in self._processed:
            existing = self._processed[supplier_ref]
            logger.info(
                "CREDIT replay blocked supplier_ref=%s existing_tx=%s",
                supplier_ref, existing.tx_id,
            )
            return already_processed_result(
                tx_type=TransactionType.CREDIT,
                tx_id=existing.tx_id,
                balance=self.balance,
            )

        self._cash_balance += amount
        result = success_result(
            tx_type=TransactionType.CREDIT,
            balance=self.balance,
            cash_usage=amount,
        )
        self._processed[supplier_ref] = result
        logger.info(
            "CREDIT applied supplier_ref=%s amount=%s new_balance=%s",
            supplier_ref, amount, self._cash_balance,
        )
        return result

    def rollback(self, rollback_ref: str, original_bet_ref: str) -> TransactionResult:
        """
        Rollback (refund) an incomplete game round.

        Idempotent: replaying the same rollback_ref returns ALREADY_PROCESSED.
        If the original bet was already refunded, returns ALREADY_REFUNDED.
        """
        if rollback_ref in self._processed:
            existing = self._processed[rollback_ref]
            logger.info(
                "ROLLBACK replay blocked rollback_ref=%s existing_tx=%s",
                rollback_ref, existing.tx_id,
            )
            return already_processed_result(
                tx_type=TransactionType.REFUND,
                tx_id=existing.tx_id,
                balance=self.balance,
                refunded=True,
            )

        if original_bet_ref not in self._debits:
            raise ValueError(
                f"Cannot rollback: original bet ref {original_bet_ref!r} not found"
            )

        original_amount = self._debits.pop(original_bet_ref)
        self._cash_balance += original_amount
        result = success_result(
            tx_type=TransactionType.REFUND,
            balance=self.balance,
            cash_usage=original_amount,
        )
        self._processed[rollback_ref] = result
        logger.info(
            "ROLLBACK applied rollback_ref=%s original_bet_ref=%s amount=%s new_balance=%s",
            rollback_ref, original_bet_ref, original_amount, self._cash_balance,
        )
        return result


# ---------------------------------------------------------------------------
# Scenario helpers
# ---------------------------------------------------------------------------


def make_wallet(initial_balance: Decimal = Decimal("10000")) -> GameWallet:
    """Return a wallet with the given starting balance (in minor units / cents)."""
    return GameWallet(
        player_id=str(uuid.uuid4()),
        currency="EUR",
        initial_balance=initial_balance,
    )


# ---------------------------------------------------------------------------
# Scenario 1 — Bet callback → verify debit applied once
# ---------------------------------------------------------------------------


def scenario_1_bet_debits_once() -> None:
    logger.info("=== Scenario 1: Bet callback → debit applied once ===")
    wallet = make_wallet(initial_balance=Decimal("10000"))  # €100.00
    bet_ref = "BET-S1-001"
    stake = Decimal("500")  # €5.00

    result = wallet.debit(bet_ref, stake)

    assert result.status == TransactionStatus.SUCCESS
    assert wallet.cash == Decimal("9500"), f"Expected 9500, got {wallet.cash}"
    logger.info("Scenario 1 PASSED — balance=%s", wallet.cash)


# ---------------------------------------------------------------------------
# Scenario 2 — SAME bet callback replayed → no double-debit
# ---------------------------------------------------------------------------


def scenario_2_bet_replay_no_double_debit() -> None:
    logger.info("=== Scenario 2: Same bet callback replayed — no double-debit ===")
    wallet = make_wallet(initial_balance=Decimal("10000"))
    bet_ref = "BET-S2-001"
    stake = Decimal("500")

    first_result = wallet.debit(bet_ref, stake)
    assert first_result.status == TransactionStatus.SUCCESS
    balance_after_first = wallet.cash  # 9500

    # Replay the same bet callback
    replay_result = wallet.debit(bet_ref, stake)

    assert replay_result.status == TransactionStatus.ALREADY_PROCESSED, (
        f"Expected ALREADY_PROCESSED, got {replay_result.status}"
    )
    assert wallet.cash == balance_after_first, (
        f"Balance changed on replay! Before={balance_after_first} After={wallet.cash}"
    )
    logger.info("Scenario 2 PASSED — balance unchanged at %s", wallet.cash)


# ---------------------------------------------------------------------------
# Scenario 3 — Win callback → verify credit applied once
# ---------------------------------------------------------------------------


def scenario_3_win_credits_once() -> None:
    logger.info("=== Scenario 3: Win callback → credit applied once ===")
    wallet = make_wallet(initial_balance=Decimal("10000"))
    bet_ref = "BET-S3-001"
    win_ref = "WIN-S3-001"
    stake = Decimal("500")
    win_amount = Decimal("1500")  # 3× win

    wallet.debit(bet_ref, stake)
    pre_win_balance = wallet.cash  # 9500

    win_result = wallet.credit(win_ref, bet_ref, win_amount)

    assert win_result.status == TransactionStatus.SUCCESS
    expected = pre_win_balance + win_amount
    assert wallet.cash == expected, f"Expected {expected}, got {wallet.cash}"
    logger.info("Scenario 3 PASSED — balance after win=%s", wallet.cash)


# ---------------------------------------------------------------------------
# Scenario 4 — SAME win callback replayed → no double-credit
# ---------------------------------------------------------------------------


def scenario_4_win_replay_no_double_credit() -> None:
    logger.info("=== Scenario 4: Same win callback replayed — no double-credit ===")
    wallet = make_wallet(initial_balance=Decimal("10000"))
    bet_ref = "BET-S4-001"
    win_ref = "WIN-S4-001"
    stake = Decimal("500")
    win_amount = Decimal("1500")

    wallet.debit(bet_ref, stake)
    wallet.credit(win_ref, bet_ref, win_amount)
    balance_after_first_win = wallet.cash  # 9500 + 1500 = 11000

    # Replay the same win callback
    replay_result = wallet.credit(win_ref, bet_ref, win_amount)

    assert replay_result.status == TransactionStatus.ALREADY_PROCESSED, (
        f"Expected ALREADY_PROCESSED, got {replay_result.status}"
    )
    assert wallet.cash == balance_after_first_win, (
        f"Balance changed on win replay! Before={balance_after_first_win} After={wallet.cash}"
    )
    logger.info("Scenario 4 PASSED — balance unchanged at %s", wallet.cash)


# ---------------------------------------------------------------------------
# Scenario 5 — Rollback → verify reversal applied once
# ---------------------------------------------------------------------------


def scenario_5_rollback_reversal() -> None:
    logger.info("=== Scenario 5: Rollback → reversal applied once ===")
    wallet = make_wallet(initial_balance=Decimal("10000"))
    bet_ref = "BET-S5-001"
    rollback_ref = "RB-S5-001"
    stake = Decimal("800")

    wallet.debit(bet_ref, stake)
    assert wallet.cash == Decimal("9200")

    rb_result = wallet.rollback(rollback_ref, original_bet_ref=bet_ref)

    assert rb_result.status == TransactionStatus.SUCCESS
    assert wallet.cash == Decimal("10000"), (
        f"Expected 10000 after rollback, got {wallet.cash}"
    )
    logger.info("Scenario 5 PASSED — balance restored to %s", wallet.cash)


# ---------------------------------------------------------------------------
# Scenario 6 — SAME rollback replayed → idempotent
# ---------------------------------------------------------------------------


def scenario_6_rollback_replay_idempotent() -> None:
    logger.info("=== Scenario 6: Same rollback replayed — idempotent ===")
    wallet = make_wallet(initial_balance=Decimal("10000"))
    bet_ref = "BET-S6-001"
    rollback_ref = "RB-S6-001"
    stake = Decimal("800")

    wallet.debit(bet_ref, stake)
    wallet.rollback(rollback_ref, original_bet_ref=bet_ref)
    balance_after_first_rollback = wallet.cash  # 10000

    # Replay the same rollback
    replay_result = wallet.rollback(rollback_ref, original_bet_ref=bet_ref)

    assert replay_result.status in {
        TransactionStatus.ALREADY_PROCESSED,
        TransactionStatus.ALREADY_REFUNDED,
    }, f"Expected ALREADY_PROCESSED/REFUNDED, got {replay_result.status}"

    assert wallet.cash == balance_after_first_rollback, (
        f"Balance changed on rollback replay! Before={balance_after_first_rollback} After={wallet.cash}"
    )
    logger.info("Scenario 6 PASSED — balance unchanged at %s", wallet.cash)


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------


def main() -> None:
    logger.info("Starting Supplier Callback Replay Integrity Proof")
    logger.info("=" * 60)

    scenarios = [
        ("Scenario 1: Bet debits balance once", scenario_1_bet_debits_once),
        ("Scenario 2: Bet replay blocked (no double-debit)", scenario_2_bet_replay_no_double_debit),
        ("Scenario 3: Win credits balance once", scenario_3_win_credits_once),
        ("Scenario 4: Win replay blocked (no double-credit)", scenario_4_win_replay_no_double_credit),
        ("Scenario 5: Rollback restores balance", scenario_5_rollback_reversal),
        ("Scenario 6: Rollback replay is idempotent", scenario_6_rollback_replay_idempotent),
    ]

    results: list[tuple[str, bool, str]] = []
    for name, fn in scenarios:
        try:
            fn()
            results.append((name, True, ""))
        except (AssertionError, Exception) as exc:
            results.append((name, False, str(exc)))
            logger.error("FAILED %s: %s", name, exc)

    logger.info("=" * 60)
    logger.info("RESULTS:")
    passed = 0
    for name, ok, err in results:
        status = "PASS" if ok else "FAIL"
        logger.info("  [%s] %s %s", status, name, f"— {err}" if err else "")
        if ok:
            passed += 1

    logger.info("%d/%d scenarios passed", passed, len(results))
    if passed < len(results):
        sys.exit(1)


if __name__ == "__main__":
    main()


# ---------------------------------------------------------------------------
# Pytest test class (run via: pytest supplier_callback_replay.py -v)
# ---------------------------------------------------------------------------


import pytest


class TestSupplierCallbackReplay:
    """Pytest-runnable version of the six game round integrity scenarios."""

    def test_scenario_1_bet_debits_once(self):
        scenario_1_bet_debits_once()

    def test_scenario_2_bet_replay_no_double_debit(self):
        scenario_2_bet_replay_no_double_debit()

    def test_scenario_3_win_credits_once(self):
        scenario_3_win_credits_once()

    def test_scenario_4_win_replay_no_double_credit(self):
        scenario_4_win_replay_no_double_credit()

    def test_scenario_5_rollback_reversal(self):
        scenario_5_rollback_reversal()

    def test_scenario_6_rollback_replay_idempotent(self):
        scenario_6_rollback_replay_idempotent()

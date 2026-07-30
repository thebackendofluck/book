#!/usr/bin/env python3
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
Release Gate: PSP Callback Replay
===================================

Replays recorded Payment Service Provider callbacks and verifies:
  1. No double-crediting — replaying the same deposit callback twice does not
     credit the player wallet twice
  2. Idempotency — duplicate callbacks return the same response
  3. Refund integrity — refund callbacks reverse the original deposit exactly
  4. Status consistency — final wallet balance matches expected after all
     callbacks in sequence

This is a pre-release gate: if any check fails, the release is blocked.

Usage:
    python psp_callback_replay.py                  # Run all checks
    python psp_callback_replay.py --json           # JSON report

Exit codes:
    0 — All checks passed
    1 — One or more checks failed
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("psp-callback-replay")


# ---------------------------------------------------------------------------
# Simulated PSP callback types
# ---------------------------------------------------------------------------


class PSPCallbackType(str, Enum):
    DEPOSIT_SUCCESS = "deposit_success"
    DEPOSIT_FAILED = "deposit_failed"
    WITHDRAWAL_SUCCESS = "withdrawal_success"
    WITHDRAWAL_FAILED = "withdrawal_failed"
    REFUND = "refund"
    CHARGEBACK = "chargeback"


# ---------------------------------------------------------------------------
# Simulated wallet (tracks balance and processed transaction IDs)
# ---------------------------------------------------------------------------


class SimulatedWallet:
    """
    In-memory wallet that mimics the platform's idempotent crediting logic.

    Each credit/debit operation checks the transaction_id against a set of
    already-processed IDs. If the ID was already seen, the operation is a
    no-op (returns the current balance without modifying it).
    """

    def __init__(self, player_id: str, initial_balance: float = 0.0):
        self.player_id = player_id
        self.balance = initial_balance
        self._processed: set[str] = set()
        self._ledger: list[dict] = []

    def credit(self, transaction_id: str, amount: float) -> tuple[float, bool]:
        """Credit the wallet. Returns (new_balance, was_duplicate)."""
        if transaction_id in self._processed:
            return self.balance, True
        self._processed.add(transaction_id)
        self.balance += amount
        self._ledger.append({
            "type": "credit",
            "txn_id": transaction_id,
            "amount": amount,
            "balance_after": self.balance,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        return self.balance, False

    def debit(self, transaction_id: str, amount: float) -> tuple[float, bool]:
        """Debit the wallet. Returns (new_balance, was_duplicate)."""
        if transaction_id in self._processed:
            return self.balance, True
        self._processed.add(transaction_id)
        self.balance -= amount
        self._ledger.append({
            "type": "debit",
            "txn_id": transaction_id,
            "amount": amount,
            "balance_after": self.balance,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        return self.balance, False

    def has_processed(self, transaction_id: str) -> bool:
        return transaction_id in self._processed


# ---------------------------------------------------------------------------
# PSP Callback Processor
# ---------------------------------------------------------------------------


class PSPCallbackProcessor:
    """Processes PSP callbacks and routes to the appropriate wallet operation."""

    def __init__(self):
        self._wallets: dict[str, SimulatedWallet] = {}
        self._metrics = {
            "total": 0,
            "credits": 0,
            "debits": 0,
            "duplicates": 0,
            "rejected": 0,
        }

    def get_or_create_wallet(
        self, player_id: str, initial_balance: float = 0.0,
    ) -> SimulatedWallet:
        if player_id not in self._wallets:
            self._wallets[player_id] = SimulatedWallet(player_id, initial_balance)
        return self._wallets[player_id]

    def process_callback(self, callback: dict) -> dict:
        """Process a PSP callback and return a result dict."""
        self._metrics["total"] += 1
        cb_type = callback.get("callback_type", "")
        txn_id = callback.get("transaction_id", "")
        player_id = callback.get("player_id", "")
        amount = callback.get("amount", 0.0)

        if not txn_id or not player_id:
            self._metrics["rejected"] += 1
            return {"status": "rejected", "reason": "missing fields"}

        wallet = self.get_or_create_wallet(player_id)

        if cb_type == PSPCallbackType.DEPOSIT_SUCCESS.value:
            balance, dup = wallet.credit(txn_id, amount)
            if dup:
                self._metrics["duplicates"] += 1
            else:
                self._metrics["credits"] += 1
            return {
                "status": "duplicate" if dup else "credited",
                "balance": balance,
                "transaction_id": txn_id,
            }

        elif cb_type == PSPCallbackType.REFUND.value:
            ref_txn_id = callback.get("original_transaction_id", "")
            balance, dup = wallet.debit(txn_id, amount)
            if dup:
                self._metrics["duplicates"] += 1
            else:
                self._metrics["debits"] += 1
            return {
                "status": "duplicate" if dup else "refunded",
                "balance": balance,
                "transaction_id": txn_id,
            }

        elif cb_type == PSPCallbackType.WITHDRAWAL_SUCCESS.value:
            balance, dup = wallet.debit(txn_id, amount)
            if dup:
                self._metrics["duplicates"] += 1
            else:
                self._metrics["debits"] += 1
            return {
                "status": "duplicate" if dup else "debited",
                "balance": balance,
                "transaction_id": txn_id,
            }

        elif cb_type in (
            PSPCallbackType.DEPOSIT_FAILED.value,
            PSPCallbackType.WITHDRAWAL_FAILED.value,
        ):
            return {"status": "acknowledged", "transaction_id": txn_id}

        else:
            self._metrics["rejected"] += 1
            return {"status": "rejected", "reason": f"unknown type: {cb_type}"}


# ---------------------------------------------------------------------------
# Check results
# ---------------------------------------------------------------------------


@dataclass
class CheckResult:
    name: str
    passed: bool
    message: str = ""
    duration_ms: float = 0.0


@dataclass
class ReplayReport:
    checks: list[CheckResult] = field(default_factory=list)
    passed: int = 0
    failed: int = 0

    def add(self, result: CheckResult) -> None:
        self.checks.append(result)
        if result.passed:
            self.passed += 1
        else:
            self.failed += 1

    @property
    def all_passed(self) -> bool:
        return self.failed == 0

    def to_dict(self) -> dict:
        return {
            "checks_passed": self.passed,
            "checks_failed": self.failed,
            "all_passed": self.all_passed,
            "checks": [
                {"name": c.name, "passed": c.passed, "message": c.message}
                for c in self.checks
            ],
        }


# ---------------------------------------------------------------------------
# Check functions
# ---------------------------------------------------------------------------


FIXTURE_CALLBACKS = [
    {
        "callback_type": "deposit_success",
        "transaction_id": "DEP-001",
        "player_id": "PLR-1",
        "amount": 100.00,
    },
    {
        "callback_type": "deposit_success",
        "transaction_id": "DEP-002",
        "player_id": "PLR-1",
        "amount": 50.00,
    },
    {
        "callback_type": "withdrawal_success",
        "transaction_id": "WDR-001",
        "player_id": "PLR-1",
        "amount": 30.00,
    },
]


def check_no_double_crediting(report: ReplayReport) -> None:
    """Depositing the same transaction twice must not double the balance."""
    t0 = time.monotonic()
    proc = PSPCallbackProcessor()
    proc.get_or_create_wallet("PLR-1", 0.0)

    cb = {
        "callback_type": "deposit_success",
        "transaction_id": "DEP-DOUBLE-001",
        "player_id": "PLR-1",
        "amount": 100.00,
    }

    r1 = proc.process_callback(cb)
    r2 = proc.process_callback(cb)

    wallet = proc.get_or_create_wallet("PLR-1")
    no_double = wallet.balance == 100.00 and r2["status"] == "duplicate"

    report.add(CheckResult(
        name="no_double_crediting",
        passed=no_double,
        message=(
            f"Balance={wallet.balance}, second call status={r2['status']}"
            if not no_double
            else "Single credit applied, duplicate detected"
        ),
        duration_ms=(time.monotonic() - t0) * 1000,
    ))


def check_idempotency_responses(report: ReplayReport) -> None:
    """Duplicate callbacks must return the same balance."""
    t0 = time.monotonic()
    proc = PSPCallbackProcessor()
    proc.get_or_create_wallet("PLR-2", 0.0)

    cb = {
        "callback_type": "deposit_success",
        "transaction_id": "DEP-IDEM-001",
        "player_id": "PLR-2",
        "amount": 75.00,
    }

    r1 = proc.process_callback(cb)
    r2 = proc.process_callback(cb)

    same_balance = r1["balance"] == r2["balance"]
    report.add(CheckResult(
        name="idempotency_same_balance",
        passed=same_balance,
        message=(
            f"First: {r1['balance']}, Second: {r2['balance']}"
            if not same_balance
            else "Identical balance returned on replay"
        ),
        duration_ms=(time.monotonic() - t0) * 1000,
    ))


def check_refund_integrity(report: ReplayReport) -> None:
    """A refund must exactly reverse the original deposit amount."""
    t0 = time.monotonic()
    proc = PSPCallbackProcessor()
    proc.get_or_create_wallet("PLR-3", 0.0)

    # Deposit
    proc.process_callback({
        "callback_type": "deposit_success",
        "transaction_id": "DEP-REF-001",
        "player_id": "PLR-3",
        "amount": 200.00,
    })

    # Refund
    proc.process_callback({
        "callback_type": "refund",
        "transaction_id": "REF-001",
        "original_transaction_id": "DEP-REF-001",
        "player_id": "PLR-3",
        "amount": 200.00,
    })

    wallet = proc.get_or_create_wallet("PLR-3")
    balance_zero = wallet.balance == 0.0

    report.add(CheckResult(
        name="refund_reverses_deposit",
        passed=balance_zero,
        message=(
            f"Balance after refund: {wallet.balance}"
            if not balance_zero
            else "Refund correctly zeroed balance"
        ),
        duration_ms=(time.monotonic() - t0) * 1000,
    ))


def check_sequence_consistency(report: ReplayReport) -> None:
    """After a sequence of deposits and withdrawals, balance must be correct."""
    t0 = time.monotonic()
    proc = PSPCallbackProcessor()
    proc.get_or_create_wallet("PLR-4", 0.0)

    for cb in FIXTURE_CALLBACKS:
        cb_copy = dict(cb)
        cb_copy["player_id"] = "PLR-4"
        proc.process_callback(cb_copy)

    wallet = proc.get_or_create_wallet("PLR-4")
    expected = 100.00 + 50.00 - 30.00  # 120.00

    correct = abs(wallet.balance - expected) < 0.01
    report.add(CheckResult(
        name="sequence_balance_correct",
        passed=correct,
        message=(
            f"Expected {expected}, got {wallet.balance}"
            if not correct
            else f"Balance {wallet.balance} matches expected {expected}"
        ),
        duration_ms=(time.monotonic() - t0) * 1000,
    ))


def check_refund_idempotency(report: ReplayReport) -> None:
    """Replaying a refund callback must not double-debit."""
    t0 = time.monotonic()
    proc = PSPCallbackProcessor()
    proc.get_or_create_wallet("PLR-5", 500.0)

    refund = {
        "callback_type": "refund",
        "transaction_id": "REF-IDEM-001",
        "original_transaction_id": "DEP-ORIG-001",
        "player_id": "PLR-5",
        "amount": 100.00,
    }

    proc.process_callback(refund)
    proc.process_callback(refund)  # replay

    wallet = proc.get_or_create_wallet("PLR-5")
    correct = wallet.balance == 400.0

    report.add(CheckResult(
        name="refund_idempotency",
        passed=correct,
        message=(
            f"Balance: {wallet.balance}, expected 400.0"
            if not correct
            else "Refund correctly applied once despite replay"
        ),
        duration_ms=(time.monotonic() - t0) * 1000,
    ))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run_replay(as_json: bool = False) -> bool:
    report = ReplayReport()

    logger.info("=== PSP Callback Replay Gate ===")

    check_no_double_crediting(report)
    check_idempotency_responses(report)
    check_refund_integrity(report)
    check_sequence_consistency(report)
    check_refund_idempotency(report)

    if as_json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(f"\n{'='*60}")
        print(f"PSP Callback Replay Report")
        print(f"{'='*60}")
        print(f"Checks passed:  {report.passed}")
        print(f"Checks failed:  {report.failed}")
        print(f"{'='*60}")
        for check in report.checks:
            icon = "PASS" if check.passed else "FAIL"
            print(f"  [{icon}] {check.name}: {check.message}")
        print(f"{'='*60}")
        print(f"Result: {'ALL PASSED' if report.all_passed else 'FAILED'}")

    return report.all_passed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PSP Callback Replay Gate")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    success = run_replay(as_json=args.json)
    sys.exit(0 if success else 1)

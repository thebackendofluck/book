# Companion code for "The Backend of Luck" - Chapter 36, Financial Operations.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Daily Reconciliation Runner

Purpose
-------
A standalone batch script that runs the full daily reconciliation pipeline,
combining three distinct checks:

  1. Wallet vs Ledger — compares the wallet service's player balance table
     against the sum of ledger entries for each player. Any mismatch is a
     critical integrity failure.

  2. PSP Settlement vs Ledger — compares each PSP's settlement report against
     the corresponding ledger credits/debits to ensure the operator's books
     reflect what the PSP settled.

  3. Orphaned Entries — scans for ledger entries that have no matching
     business event (no payment_id, no game round id). These are indicators
     of bugs or manual adjustments that bypassed proper booking.

Output
------
All findings are collected into a ReconciliationReport dataclass that is
printed to stdout and can be serialised to JSON for downstream alerting.

Usage
-----
    # Dry run using in-memory stubs (no external dependencies)
    python reconciliation_runner.py

    # Run as pytest
    pytest reconciliation_runner.py -v
"""

from __future__ import annotations

import json
import logging
import sys
import os
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models import (
    AccountType,
    Balance,
    Direction,
    LedgerAccount,
    LedgerEntry,
    ReconciliationResult,
    RunResult,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types for this runner's internal representation
# ---------------------------------------------------------------------------


@dataclass
class PlayerWalletBalance:
    """
    Wallet service snapshot for one player.

    In production, loaded from the wallet service database.
    For this demo, seeded in-memory.
    """
    player_id: str
    balance: int   # minor units (cents)
    currency: str


@dataclass
class LedgerEntryRecord:
    """Flat representation of a ledger entry for reconciliation queries."""
    entry_id: str
    account_id: str
    account_type: str    # AccountType value
    amount: int
    direction: str       # Direction value
    reference: Optional[str]     # business event reference (payment_id, round_id, etc.)
    player_id: Optional[str]
    created_at: datetime


@dataclass
class PSPSettlementLine:
    """One line from a PSP's daily settlement report."""
    external_id: str       # PSP transaction reference
    amount: int            # minor units
    currency: str
    transaction_type: str  # "deposit" | "withdrawal" | "refund"
    status: str            # "Settled" | "Reversed" etc.


@dataclass
class DiscrepancyRecord:
    kind: str              # WALLET_LEDGER_MISMATCH | PSP_LEDGER_MISMATCH | ORPHANED_ENTRY
    entity_id: str         # player_id, external_id, or entry_id
    detail: str
    amount_a: Optional[int] = None
    amount_b: Optional[int] = None
    delta: Optional[int] = None


@dataclass
class ReconciliationReport:
    """Full output of one reconciliation run."""
    run_date: str                      # YYYY-MM-DD
    report_for: str                    # date that was reconciled
    wallet_ledger_checks: int = 0
    wallet_ledger_mismatches: int = 0
    psp_ledger_checks: int = 0
    psp_ledger_mismatches: int = 0
    orphaned_entries: int = 0
    total_discrepancy_amount: int = 0
    discrepancies: list[DiscrepancyRecord] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    completed_at: Optional[str] = None

    @property
    def is_clean(self) -> bool:
        return (
            self.wallet_ledger_mismatches == 0
            and self.psp_ledger_mismatches == 0
            and self.orphaned_entries == 0
        )


# ---------------------------------------------------------------------------
# In-memory store (replace with real DB adapters in production)
# ---------------------------------------------------------------------------


class InMemoryWalletStore:
    """Stub wallet balance store."""

    def __init__(self) -> None:
        self._balances: dict[str, PlayerWalletBalance] = {}

    def upsert(self, balance: PlayerWalletBalance) -> None:
        self._balances[balance.player_id] = balance

    def get_all(self) -> list[PlayerWalletBalance]:
        return list(self._balances.values())

    def get_player(self, player_id: str) -> Optional[PlayerWalletBalance]:
        return self._balances.get(player_id)


class InMemoryLedgerStore:
    """Stub ledger entry store."""

    def __init__(self) -> None:
        self._entries: list[LedgerEntryRecord] = []

    def add(self, entry: LedgerEntryRecord) -> None:
        self._entries.append(entry)

    def get_entries_for_player(self, player_id: str) -> list[LedgerEntryRecord]:
        return [e for e in self._entries if e.player_id == player_id]

    def get_entries_for_account_type(self, account_type: str) -> list[LedgerEntryRecord]:
        return [e for e in self._entries if e.account_type == account_type]

    def get_all(self) -> list[LedgerEntryRecord]:
        return list(self._entries)

    def compute_balance_for_player(self, player_id: str) -> int:
        """
        Net ledger balance for a player wallet account.

        Convention: CREDIT entries increase balance, DEBIT entries decrease it.
        (Player wallet accounts are liability accounts from the operator's perspective.)
        """
        entries = self.get_entries_for_player(player_id)
        total = 0
        for e in entries:
            if e.account_type == AccountType.PLAYER_WALLET.value:
                if e.direction == Direction.CREDIT.value:
                    total += e.amount
                else:
                    total -= e.amount
        return total


class InMemoryPSPSettlementStore:
    """Stub PSP settlement report store."""

    def __init__(self) -> None:
        self._lines: dict[str, list[PSPSettlementLine]] = {}  # psp_name → lines

    def load_report(self, psp_name: str, lines: list[PSPSettlementLine]) -> None:
        self._lines[psp_name] = lines

    def get_report(self, psp_name: str) -> list[PSPSettlementLine]:
        return self._lines.get(psp_name, [])

    def list_psps(self) -> list[str]:
        return list(self._lines.keys())


# ---------------------------------------------------------------------------
# Reconciliation engine
# ---------------------------------------------------------------------------


class DailyReconciliationRunner:
    """
    Orchestrates the three reconciliation checks for a given report date.

    Inject your real store implementations in production.
    """

    def __init__(
        self,
        wallet_store: InMemoryWalletStore,
        ledger_store: InMemoryLedgerStore,
        psp_store: InMemoryPSPSettlementStore,
    ) -> None:
        self._wallets = wallet_store
        self._ledger = ledger_store
        self._psp = psp_store

    # ------------------------------------------------------------------
    # Check 1: Wallet balance vs Ledger sum per player
    # ------------------------------------------------------------------

    def check_wallet_vs_ledger(
        self, report: ReconciliationReport
    ) -> None:
        """
        For each player, compare the wallet service's stored balance against
        the sum of all PLAYER_WALLET ledger entries.

        A mismatch means either:
          a) The wallet service applied a credit/debit that was never booked
             to the ledger (booking gap), OR
          b) The ledger has entries with no corresponding wallet movement
             (ghost posting).

        Both are CRITICAL and require immediate investigation.
        """
        logger.info("Check 1: Wallet vs Ledger balances")
        players = self._wallets.get_all()
        report.wallet_ledger_checks = len(players)

        for player in players:
            ledger_balance = self._ledger.compute_balance_for_player(player.player_id)
            wallet_balance = player.balance

            if ledger_balance != wallet_balance:
                delta = abs(ledger_balance - wallet_balance)
                report.wallet_ledger_mismatches += 1
                report.total_discrepancy_amount += delta
                disc = DiscrepancyRecord(
                    kind="WALLET_LEDGER_MISMATCH",
                    entity_id=player.player_id,
                    detail=(
                        f"Wallet balance={wallet_balance} "
                        f"Ledger sum={ledger_balance} "
                        f"delta={delta} "
                        f"currency={player.currency}"
                    ),
                    amount_a=wallet_balance,
                    amount_b=ledger_balance,
                    delta=delta,
                )
                report.discrepancies.append(disc)
                logger.error(
                    "WALLET_LEDGER_MISMATCH player=%s wallet=%d ledger=%d delta=%d",
                    player.player_id, wallet_balance, ledger_balance, delta,
                )
            else:
                logger.debug("Player %s: wallet/ledger match at %d", player.player_id, wallet_balance)

    # ------------------------------------------------------------------
    # Check 2: PSP settlement report vs Ledger entries
    # ------------------------------------------------------------------

    def check_psp_vs_ledger(self, report: ReconciliationReport) -> None:
        """
        For each PSP, compare their settlement report against the corresponding
        PSP_CLEARING ledger entries.

        Each settled transaction should appear as a CREDIT on the
        PSP_CLEARING account in the ledger (representing money owed by the PSP
        to the operator). If amounts differ or entries are missing on either
        side, a discrepancy is raised.
        """
        logger.info("Check 2: PSP settlement vs Ledger PSP_CLEARING entries")

        for psp_name in self._psp.list_psps():
            settlement_lines = self._psp.get_report(psp_name)
            ledger_entries = {
                e.reference: e
                for e in self._ledger.get_entries_for_account_type(AccountType.PSP_CLEARING.value)
                if e.reference  # skip entries with no reference
            }

            for line in settlement_lines:
                report.psp_ledger_checks += 1
                ledger_entry = ledger_entries.get(line.external_id)

                if ledger_entry is None:
                    # Settlement present in PSP report but not booked in ledger
                    delta = line.amount
                    report.psp_ledger_mismatches += 1
                    report.total_discrepancy_amount += delta
                    disc = DiscrepancyRecord(
                        kind="PSP_LEDGER_MISMATCH",
                        entity_id=line.external_id,
                        detail=(
                            f"PSP {psp_name!r} settled {line.external_id!r} "
                            f"for amount={line.amount} but no matching ledger entry found"
                        ),
                        amount_a=None,
                        amount_b=line.amount,
                        delta=delta,
                    )
                    report.discrepancies.append(disc)
                    logger.error(
                        "PSP_LEDGER_MISMATCH psp=%s ext_id=%s amount=%d — not in ledger",
                        psp_name, line.external_id, line.amount,
                    )
                elif ledger_entry.amount != line.amount:
                    delta = abs(ledger_entry.amount - line.amount)
                    report.psp_ledger_mismatches += 1
                    report.total_discrepancy_amount += delta
                    disc = DiscrepancyRecord(
                        kind="PSP_LEDGER_MISMATCH",
                        entity_id=line.external_id,
                        detail=(
                            f"PSP {psp_name!r} amount={line.amount} "
                            f"but ledger entry amount={ledger_entry.amount} "
                            f"delta={delta}"
                        ),
                        amount_a=ledger_entry.amount,
                        amount_b=line.amount,
                        delta=delta,
                    )
                    report.discrepancies.append(disc)
                    logger.error(
                        "PSP_LEDGER_AMOUNT_MISMATCH psp=%s ext_id=%s psp_amount=%d ledger_amount=%d",
                        psp_name, line.external_id, line.amount, ledger_entry.amount,
                    )
                else:
                    logger.debug(
                        "PSP %s ext_id=%s amount=%d: MATCHED",
                        psp_name, line.external_id, line.amount,
                    )

    # ------------------------------------------------------------------
    # Check 3: Orphaned ledger entries
    # ------------------------------------------------------------------

    def check_orphaned_entries(self, report: ReconciliationReport) -> None:
        """
        Scan for ledger entries that have no matching business event reference.

        Orphaned entries (reference=None or reference not in known business
        events) indicate:
          - Manual DB corrections that bypassed the booking layer
          - Bugs where an entry was posted without a corresponding event

        These don't always indicate a financial discrepancy but are always
        worth investigating for audit/compliance purposes.
        """
        logger.info("Check 3: Orphaned ledger entries (no business event reference)")
        all_entries = self._ledger.get_all()

        for entry in all_entries:
            if not entry.reference:
                report.orphaned_entries += 1
                disc = DiscrepancyRecord(
                    kind="ORPHANED_ENTRY",
                    entity_id=entry.entry_id,
                    detail=(
                        f"Ledger entry {entry.entry_id!r} "
                        f"account_type={entry.account_type} "
                        f"amount={entry.amount} "
                        f"direction={entry.direction} "
                        f"has no business event reference"
                    ),
                    amount_a=entry.amount,
                )
                report.discrepancies.append(disc)
                logger.warning(
                    "ORPHANED_ENTRY entry_id=%s account_type=%s amount=%d",
                    entry.entry_id, entry.account_type, entry.amount,
                )

    # ------------------------------------------------------------------
    # Main run method
    # ------------------------------------------------------------------

    def run(self, report_date: date) -> ReconciliationReport:
        """Execute all three reconciliation checks and return a consolidated report."""
        run_start = datetime.now(timezone.utc)
        logger.info("Starting reconciliation run for date=%s", report_date)
        logger.info("=" * 60)

        report = ReconciliationReport(
            run_date=run_start.date().isoformat(),
            report_for=report_date.isoformat(),
        )

        try:
            self.check_wallet_vs_ledger(report)
        except Exception as exc:
            msg = f"check_wallet_vs_ledger failed: {exc}"
            logger.exception(msg)
            report.errors.append(msg)

        try:
            self.check_psp_vs_ledger(report)
        except Exception as exc:
            msg = f"check_psp_vs_ledger failed: {exc}"
            logger.exception(msg)
            report.errors.append(msg)

        try:
            self.check_orphaned_entries(report)
        except Exception as exc:
            msg = f"check_orphaned_entries failed: {exc}"
            logger.exception(msg)
            report.errors.append(msg)

        report.completed_at = datetime.now(timezone.utc).isoformat()

        logger.info("=" * 60)
        if report.is_clean:
            logger.info("Reconciliation CLEAN — no discrepancies found")
        else:
            logger.warning(
                "Reconciliation ISSUES: wallet_mismatches=%d psp_mismatches=%d orphans=%d total_delta=%d",
                report.wallet_ledger_mismatches,
                report.psp_ledger_mismatches,
                report.orphaned_entries,
                report.total_discrepancy_amount,
            )

        return report


# ---------------------------------------------------------------------------
# Demo data builder
# ---------------------------------------------------------------------------


def _build_demo_stores() -> tuple[InMemoryWalletStore, InMemoryLedgerStore, InMemoryPSPSettlementStore]:
    """
    Construct seeded stores that exercise all three checks:
      - One clean player (wallet == ledger sum)
      - One mismatched player (wallet != ledger sum)
      - One PSP entry matched cleanly
      - One PSP entry missing from ledger
      - One orphaned ledger entry
    """
    import uuid

    wallet_store = InMemoryWalletStore()
    ledger_store = InMemoryLedgerStore()
    psp_store = InMemoryPSPSettlementStore()

    now = datetime.now(timezone.utc)

    # ---- Player A: clean ----
    wallet_store.upsert(PlayerWalletBalance("player-A", balance=10_000, currency="EUR"))
    ledger_store.add(LedgerEntryRecord(
        entry_id=str(uuid.uuid4()),
        account_id="acc-wallet-A",
        account_type=AccountType.PLAYER_WALLET.value,
        amount=10_000,
        direction=Direction.CREDIT.value,
        reference="PAY-A-001",
        player_id="player-A",
        created_at=now,
    ))

    # ---- Player B: wallet/ledger mismatch (ledger has 8000, wallet has 10000) ----
    wallet_store.upsert(PlayerWalletBalance("player-B", balance=10_000, currency="EUR"))
    ledger_store.add(LedgerEntryRecord(
        entry_id=str(uuid.uuid4()),
        account_id="acc-wallet-B",
        account_type=AccountType.PLAYER_WALLET.value,
        amount=8_000,
        direction=Direction.CREDIT.value,
        reference="PAY-B-001",
        player_id="player-B",
        created_at=now,
    ))

    # ---- PSP Adyen: one clean entry, one missing-from-ledger entry ----
    # Clean: external_id EXT-001 → 5000 in both PSP report and ledger
    ledger_store.add(LedgerEntryRecord(
        entry_id=str(uuid.uuid4()),
        account_id="acc-adyen-clearing",
        account_type=AccountType.PSP_CLEARING.value,
        amount=5_000,
        direction=Direction.CREDIT.value,
        reference="EXT-001",
        player_id=None,
        created_at=now,
    ))
    # Missing from ledger: EXT-002 is in PSP settlement but not booked
    psp_store.load_report("adyen", [
        PSPSettlementLine("EXT-001", 5_000, "EUR", "deposit", "Settled"),
        PSPSettlementLine("EXT-002", 7_500, "EUR", "deposit", "Settled"),  # ghost
    ])

    # ---- Orphaned ledger entry (no reference) ----
    ledger_store.add(LedgerEntryRecord(
        entry_id=str(uuid.uuid4()),
        account_id="acc-operator-revenue",
        account_type=AccountType.OPERATOR_REVENUE.value,
        amount=250,
        direction=Direction.CREDIT.value,
        reference=None,   # orphan — no business event
        player_id=None,
        created_at=now,
    ))

    return wallet_store, ledger_store, psp_store


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def main() -> None:
    logger.info("Daily Reconciliation Runner")
    logger.info("=" * 60)

    wallet_store, ledger_store, psp_store = _build_demo_stores()
    runner = DailyReconciliationRunner(wallet_store, ledger_store, psp_store)

    report_date = date.today()
    report = runner.run(report_date)

    # Print JSON report
    report_dict = asdict(report)
    print("\n" + json.dumps(report_dict, indent=2, default=str))

    if not report.is_clean:
        logger.warning("Reconciliation completed with %d discrepancy(ies)", len(report.discrepancies))
        sys.exit(1)
    else:
        logger.info("Reconciliation completed cleanly")


if __name__ == "__main__":
    main()


# ---------------------------------------------------------------------------
# Pytest test suite
# ---------------------------------------------------------------------------


import pytest


@pytest.fixture
def clean_stores():
    """Stores with one fully matched player and one matched PSP entry."""
    import uuid
    ws = InMemoryWalletStore()
    ls = InMemoryLedgerStore()
    ps = InMemoryPSPSettlementStore()
    now = datetime.now(timezone.utc)

    ws.upsert(PlayerWalletBalance("player-clean", 20_000, "EUR"))
    ls.add(LedgerEntryRecord(
        entry_id=str(uuid.uuid4()),
        account_id="acc-1",
        account_type=AccountType.PLAYER_WALLET.value,
        amount=20_000,
        direction=Direction.CREDIT.value,
        reference="PAY-CLEAN",
        player_id="player-clean",
        created_at=now,
    ))
    ls.add(LedgerEntryRecord(
        entry_id=str(uuid.uuid4()),
        account_id="acc-psp-1",
        account_type=AccountType.PSP_CLEARING.value,
        amount=20_000,
        direction=Direction.CREDIT.value,
        reference="EXT-CLEAN",
        player_id=None,
        created_at=now,
    ))
    ps.load_report("adyen", [
        PSPSettlementLine("EXT-CLEAN", 20_000, "EUR", "deposit", "Settled"),
    ])
    return ws, ls, ps


@pytest.fixture
def demo_stores():
    return _build_demo_stores()


class TestReconciliationRunner:

    def test_clean_run_no_discrepancies(self, clean_stores):
        ws, ls, ps = clean_stores
        runner = DailyReconciliationRunner(ws, ls, ps)
        report = runner.run(date.today())

        assert report.is_clean
        assert report.wallet_ledger_mismatches == 0
        assert report.psp_ledger_mismatches == 0
        assert report.orphaned_entries == 0
        assert len(report.discrepancies) == 0

    def test_wallet_ledger_mismatch_detected(self, demo_stores):
        ws, ls, ps = demo_stores
        runner = DailyReconciliationRunner(ws, ls, ps)
        report = runner.run(date.today())

        assert report.wallet_ledger_mismatches >= 1
        kinds = [d.kind for d in report.discrepancies]
        assert "WALLET_LEDGER_MISMATCH" in kinds

    def test_wallet_ledger_mismatch_delta(self, demo_stores):
        ws, ls, ps = demo_stores
        runner = DailyReconciliationRunner(ws, ls, ps)
        report = runner.run(date.today())

        mismatch = next(
            d for d in report.discrepancies if d.kind == "WALLET_LEDGER_MISMATCH"
        )
        assert mismatch.entity_id == "player-B"
        assert mismatch.delta == 2_000   # 10000 - 8000

    def test_psp_ledger_mismatch_detected(self, demo_stores):
        ws, ls, ps = demo_stores
        runner = DailyReconciliationRunner(ws, ls, ps)
        report = runner.run(date.today())

        assert report.psp_ledger_mismatches >= 1
        kinds = [d.kind for d in report.discrepancies]
        assert "PSP_LEDGER_MISMATCH" in kinds

    def test_psp_missing_from_ledger(self, demo_stores):
        ws, ls, ps = demo_stores
        runner = DailyReconciliationRunner(ws, ls, ps)
        report = runner.run(date.today())

        missing = next(
            (d for d in report.discrepancies
             if d.kind == "PSP_LEDGER_MISMATCH" and d.entity_id == "EXT-002"),
            None,
        )
        assert missing is not None, "EXT-002 ghost transaction not detected"
        assert missing.amount_b == 7_500

    def test_orphaned_entry_detected(self, demo_stores):
        ws, ls, ps = demo_stores
        runner = DailyReconciliationRunner(ws, ls, ps)
        report = runner.run(date.today())

        assert report.orphaned_entries >= 1
        kinds = [d.kind for d in report.discrepancies]
        assert "ORPHANED_ENTRY" in kinds

    def test_total_discrepancy_amount(self, demo_stores):
        ws, ls, ps = demo_stores
        runner = DailyReconciliationRunner(ws, ls, ps)
        report = runner.run(date.today())

        # wallet delta=2000 + psp EXT-002 ghost=7500 = 9500
        assert report.total_discrepancy_amount == 9_500

    def test_report_date_recorded(self, clean_stores):
        ws, ls, ps = clean_stores
        runner = DailyReconciliationRunner(ws, ls, ps)
        target_date = date(2025, 6, 15)
        report = runner.run(target_date)

        assert report.report_for == "2025-06-15"

    def test_empty_stores_clean(self):
        runner = DailyReconciliationRunner(
            InMemoryWalletStore(),
            InMemoryLedgerStore(),
            InMemoryPSPSettlementStore(),
        )
        report = runner.run(date.today())

        assert report.is_clean
        assert report.wallet_ledger_checks == 0
        assert report.psp_ledger_checks == 0

    def test_completed_at_set(self, clean_stores):
        ws, ls, ps = clean_stores
        runner = DailyReconciliationRunner(ws, ls, ps)
        report = runner.run(date.today())

        assert report.completed_at is not None

    def test_multiple_players_checked(self):
        import uuid
        ws = InMemoryWalletStore()
        ls = InMemoryLedgerStore()
        ps = InMemoryPSPSettlementStore()
        now = datetime.now(timezone.utc)

        for i in range(5):
            pid = f"player-{i}"
            ws.upsert(PlayerWalletBalance(pid, 1_000 * (i + 1), "EUR"))
            ls.add(LedgerEntryRecord(
                entry_id=str(uuid.uuid4()),
                account_id=f"acc-{i}",
                account_type=AccountType.PLAYER_WALLET.value,
                amount=1_000 * (i + 1),
                direction=Direction.CREDIT.value,
                reference=f"PAY-{i}",
                player_id=pid,
                created_at=now,
            ))

        runner = DailyReconciliationRunner(ws, ls, ps)
        report = runner.run(date.today())

        assert report.wallet_ledger_checks == 5
        assert report.wallet_ledger_mismatches == 0

    def test_psp_amount_mismatch(self):
        import uuid
        ws = InMemoryWalletStore()
        ls = InMemoryLedgerStore()
        ps = InMemoryPSPSettlementStore()
        now = datetime.now(timezone.utc)

        # Ledger says 5000, PSP says 4999 — 1 cent discrepancy
        ls.add(LedgerEntryRecord(
            entry_id=str(uuid.uuid4()),
            account_id="acc-psp",
            account_type=AccountType.PSP_CLEARING.value,
            amount=5_000,
            direction=Direction.CREDIT.value,
            reference="EXT-MISMATCH",
            player_id=None,
            created_at=now,
        ))
        ps.load_report("paypal", [
            PSPSettlementLine("EXT-MISMATCH", 4_999, "EUR", "deposit", "Settled"),
        ])

        runner = DailyReconciliationRunner(ws, ls, ps)
        report = runner.run(date.today())

        assert report.psp_ledger_mismatches == 1
        assert report.total_discrepancy_amount == 1

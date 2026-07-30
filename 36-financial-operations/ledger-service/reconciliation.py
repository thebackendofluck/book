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
Ledger Service — Reconciliation Engine

Compares the ledger (source of truth) against external systems
to detect discrepancies before they become financial losses.
"""

from __future__ import annotations

from datetime import datetime, timezone

import structlog

from ledger import Ledger
from models import AccountType, ReconciliationResult, RunResult

logger = structlog.get_logger()


def _account_id(account_type: AccountType, entity_id: str) -> str:
    return f"{account_type.value}:{entity_id}"


class WalletService:
    """
    Stub for the external wallet service.
    In production, this calls the wallet microservice API.
    """

    def __init__(self) -> None:
        self._balances: dict[str, int] = {}

    def set_balance(self, player_id: str, balance: int) -> None:
        self._balances[player_id] = balance

    async def get_balance(self, player_id: str) -> int:
        return self._balances.get(player_id, 0)

    def list_player_ids(self) -> list[str]:
        return list(self._balances.keys())


class PSPService:
    """
    Stub for PSP reporting API.
    In production, this pulls settlement reports from PSP dashboards.
    """

    def __init__(self) -> None:
        self._settlements: dict[str, dict[str, int]] = {}

    def set_settlement(self, psp_name: str, date: str, amount: int) -> None:
        if psp_name not in self._settlements:
            self._settlements[psp_name] = {}
        self._settlements[psp_name][date] = amount

    async def get_settlement(self, psp_name: str, date: str) -> int:
        return self._settlements.get(psp_name, {}).get(date, 0)

    def list_psp_names(self) -> list[str]:
        return list(self._settlements.keys())


class ReconciliationEngine:
    """Reconciles ledger balances against external sources of truth."""

    def __init__(
        self,
        ledger: Ledger,
        wallet_service: WalletService | None = None,
        psp_service: PSPService | None = None,
    ) -> None:
        self.ledger = ledger
        self.wallet_service = wallet_service or WalletService()
        self.psp_service = psp_service or PSPService()

    async def reconcile_wallet_vs_ledger(
        self, player_id: str
    ) -> ReconciliationResult:
        """
        Compare the wallet service balance for a player against
        the ledger's computed balance for that player's wallet account.
        """
        account_id = _account_id(AccountType.PLAYER_WALLET, player_id)
        ledger_balance = await self.ledger.get_account_balance(account_id)

        # For PLAYER_WALLET: credits increase balance, debits decrease it.
        # Net wallet balance = total_credits - total_debits
        ledger_wallet_balance = ledger_balance.total_credits - ledger_balance.total_debits

        wallet_balance = await self.wallet_service.get_balance(player_id)

        discrepancy = abs(ledger_wallet_balance - wallet_balance)

        result = ReconciliationResult(
            is_matched=discrepancy == 0,
            source_a_label=f"ledger:{account_id}",
            source_a_balance=ledger_wallet_balance,
            source_b_label=f"wallet_service:{player_id}",
            source_b_balance=wallet_balance,
            discrepancy=discrepancy,
            details="" if discrepancy == 0 else f"Off by {discrepancy} minor units",
        )

        logger.info(
            "wallet_reconciliation",
            player_id=player_id,
            is_matched=result.is_matched,
            discrepancy=discrepancy,
        )
        return result

    async def reconcile_psp_vs_ledger(
        self, psp_name: str, date: str
    ) -> ReconciliationResult:
        """
        Compare PSP settlement report against ledger PSP clearing account.
        """
        account_id = _account_id(AccountType.PSP_CLEARING, psp_name)
        ledger_balance = await self.ledger.get_account_balance(account_id)

        # For PSP_CLEARING: debits are deposits received, credits are settlements paid.
        # Net clearing balance = total_debits - total_credits (what PSP still owes us)
        ledger_clearing = ledger_balance.total_debits - ledger_balance.total_credits

        psp_reported = await self.psp_service.get_settlement(psp_name, date)

        discrepancy = abs(ledger_clearing - psp_reported)

        result = ReconciliationResult(
            is_matched=discrepancy == 0,
            source_a_label=f"ledger:{account_id}",
            source_a_balance=ledger_clearing,
            source_b_label=f"psp_report:{psp_name}:{date}",
            source_b_balance=psp_reported,
            discrepancy=discrepancy,
            details="" if discrepancy == 0 else f"Off by {discrepancy} minor units",
        )

        logger.info(
            "psp_reconciliation",
            psp_name=psp_name,
            date=date,
            is_matched=result.is_matched,
            discrepancy=discrepancy,
        )
        return result

    async def detect_orphaned_entries(self) -> list[dict]:
        """
        Find entries that exist in the entry store but don't belong
        to any known posting. These indicate data corruption.
        """
        all_entries = await self.ledger.store.get_all_entries()
        all_postings = await self.ledger.store.get_all_postings()

        posting_entry_ids: set = set()
        for posting in all_postings:
            for entry in posting.entries:
                posting_entry_ids.add(entry.entry_id)

        orphaned = []
        for entry in all_entries:
            if entry.entry_id not in posting_entry_ids:
                orphaned.append(
                    {
                        "entry_id": str(entry.entry_id),
                        "account_id": entry.account_id,
                        "amount": entry.amount,
                        "direction": entry.direction.value,
                    }
                )

        if orphaned:
            logger.warning("orphaned_entries_detected", count=len(orphaned))

        return orphaned

    async def daily_reconciliation_run(self) -> RunResult:
        """
        Run reconciliation for all known wallets and PSPs.
        Returns an aggregate result.
        """
        results: list[ReconciliationResult] = []
        errors: list[str] = []

        # Reconcile all known wallets
        for player_id in self.wallet_service.list_player_ids():
            try:
                result = await self.reconcile_wallet_vs_ledger(player_id)
                results.append(result)
            except Exception as exc:
                errors.append(f"wallet:{player_id}: {exc}")

        # Reconcile all known PSPs
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for psp_name in self.psp_service.list_psp_names():
            try:
                result = await self.reconcile_psp_vs_ledger(psp_name, today)
                results.append(result)
            except Exception as exc:
                errors.append(f"psp:{psp_name}: {exc}")

        matched = sum(1 for r in results if r.is_matched)
        mismatched = sum(1 for r in results if not r.is_matched)

        run_result = RunResult(
            total_checked=len(results),
            matched=matched,
            mismatched=mismatched,
            errors=errors,
            results=results,
        )

        logger.info(
            "daily_reconciliation_complete",
            total=run_result.total_checked,
            matched=matched,
            mismatched=mismatched,
            errors=len(errors),
        )
        return run_result

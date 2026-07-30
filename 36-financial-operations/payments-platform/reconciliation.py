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
Daily reconciliation engine.

Reconciliation compares the platform's internal transaction records against
the settlement reports provided by each PSP and flags discrepancies.

Process:
  1. Load all platform transactions for the report date per PSP.
  2. Load the PSP's settlement file (CSV / API response).
  3. Match by external_transaction_id.
  4. Report:
     - Matched: amounts agree  → OK
     - Matched: amount differs → AMOUNT_MISMATCH
     - In platform only       → MISSING_IN_PSP (possible timing issue)
     - In PSP only            → MISSING_IN_PLATFORM (ghost transaction — critical)
  5. Produce a ReconciliationRecord summary per PSP per currency.
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterator, Optional

from models import ReconciliationRecord, TransactionType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class PlatformTransaction:
    transaction_id: str
    external_id: Optional[str]
    provider_name: str
    transaction_type: TransactionType
    amount: int
    currency: str
    status: str
    settled_at: Optional[date] = None


@dataclass
class PSPSettlementRow:
    external_id: str
    provider_name: str
    transaction_type: str
    amount: int
    currency: str
    status: str


@dataclass
class DiscrepancyItem:
    kind: str              # AMOUNT_MISMATCH | MISSING_IN_PSP | MISSING_IN_PLATFORM
    external_id: str
    platform_amount: Optional[int]
    psp_amount: Optional[int]
    currency: str
    provider_name: str
    notes: str = ""


# ---------------------------------------------------------------------------
# Settlement file parser
# ---------------------------------------------------------------------------


class SettlementFileParser:
    """
    Parses PSP settlement CSV files into PSPSettlementRow objects.

    CSV format (Adyen example):
        external_id,type,amount,currency,status
        PSP123456,deposit,5000,GBP,Settled
    """

    def parse_csv(self, content: str, provider_name: str) -> list[PSPSettlementRow]:
        rows: list[PSPSettlementRow] = []
        reader = csv.DictReader(io.StringIO(content))
        for line in reader:
            try:
                rows.append(
                    PSPSettlementRow(
                        external_id=line["external_id"],
                        provider_name=provider_name,
                        transaction_type=line.get("type", "deposit"),
                        amount=int(line["amount"]),
                        currency=line["currency"].upper(),
                        status=line.get("status", ""),
                    )
                )
            except (KeyError, ValueError) as exc:
                logger.warning("Skipping malformed settlement row: %s — %s", line, exc)
        return rows


# ---------------------------------------------------------------------------
# Platform transaction store interface
# ---------------------------------------------------------------------------


class PlatformTransactionStore:
    """
    Stub that returns in-memory transactions for testing.
    Replace with a real DB query returning settled transactions for the date.
    """

    def __init__(self) -> None:
        self._records: list[PlatformTransaction] = []

    def add(self, tx: PlatformTransaction) -> None:
        self._records.append(tx)

    def get_settled_for_date(
        self, report_date: date, provider_name: Optional[str] = None
    ) -> list[PlatformTransaction]:
        return [
            t
            for t in self._records
            if (t.settled_at == report_date or report_date is None)
            and (provider_name is None or t.provider_name == provider_name)
        ]


# ---------------------------------------------------------------------------
# Reconciliation engine
# ---------------------------------------------------------------------------


class ReconciliationEngine:
    """
    Reconciles platform records against PSP settlement data.

    Designed to run as a daily batch job (e.g. triggered by a cron / Celery beat).
    """

    def __init__(self, store: PlatformTransactionStore) -> None:
        self._store = store
        self._parser = SettlementFileParser()

    def reconcile(
        self,
        report_date: date,
        provider_name: str,
        settlement_csv: str,
    ) -> ReconciliationRecord:
        """
        Perform reconciliation for one PSP on one date.

        Returns a ReconciliationRecord with summary counts and discrepancy total.
        """
        # Load platform records
        platform_txns = self._store.get_settled_for_date(report_date, provider_name)
        platform_map: dict[str, PlatformTransaction] = {
            t.external_id: t for t in platform_txns if t.external_id
        }

        # Load PSP settlement
        psp_rows = self._parser.parse_csv(settlement_csv, provider_name)
        psp_map: dict[str, PSPSettlementRow] = {r.external_id: r for r in psp_rows}

        discrepancies: list[DiscrepancyItem] = []
        total_deposits = 0
        total_withdrawals = 0
        total_refunds = 0
        tx_count = 0

        # Check PSP rows against platform
        for ext_id, psp_row in psp_map.items():
            tx_count += 1
            if psp_row.transaction_type == "deposit":
                total_deposits += psp_row.amount
            elif psp_row.transaction_type == "withdrawal":
                total_withdrawals += psp_row.amount
            elif psp_row.transaction_type == "refund":
                total_refunds += psp_row.amount

            platform_tx = platform_map.get(ext_id)
            if platform_tx is None:
                discrepancies.append(
                    DiscrepancyItem(
                        kind="MISSING_IN_PLATFORM",
                        external_id=ext_id,
                        platform_amount=None,
                        psp_amount=psp_row.amount,
                        currency=psp_row.currency,
                        provider_name=provider_name,
                        notes="Transaction present in PSP settlement but not in platform DB",
                    )
                )
                logger.error(
                    "GHOST TRANSACTION detected: %s not in platform for PSP %s",
                    ext_id,
                    provider_name,
                )
            elif platform_tx.amount != psp_row.amount:
                discrepancies.append(
                    DiscrepancyItem(
                        kind="AMOUNT_MISMATCH",
                        external_id=ext_id,
                        platform_amount=platform_tx.amount,
                        psp_amount=psp_row.amount,
                        currency=psp_row.currency,
                        provider_name=provider_name,
                        notes=f"Platform={platform_tx.amount} PSP={psp_row.amount}",
                    )
                )

        # Platform transactions not in PSP settlement
        for ext_id, platform_tx in platform_map.items():
            if ext_id not in psp_map:
                discrepancies.append(
                    DiscrepancyItem(
                        kind="MISSING_IN_PSP",
                        external_id=ext_id,
                        platform_amount=platform_tx.amount,
                        psp_amount=None,
                        currency=platform_tx.currency,
                        provider_name=provider_name,
                        notes="May be timing — recheck next cycle",
                    )
                )

        total_discrepancy = sum(
            abs((d.psp_amount or 0) - (d.platform_amount or 0))
            for d in discrepancies
        )

        if discrepancies:
            logger.warning(
                "Reconciliation %s %s: %d discrepancies, net=%d",
                provider_name,
                report_date,
                len(discrepancies),
                total_discrepancy,
            )
        else:
            logger.info("Reconciliation %s %s: CLEAN", provider_name, report_date)

        notes = "; ".join(f"{d.kind}:{d.external_id}" for d in discrepancies[:5])
        if len(discrepancies) > 5:
            notes += f" (+{len(discrepancies) - 5} more)"

        return ReconciliationRecord(
            date=report_date.isoformat(),
            provider_name=provider_name,
            currency="GBP",  # multi-currency: run once per currency in prod
            total_deposits=total_deposits,
            total_withdrawals=total_withdrawals,
            total_refunds=total_refunds,
            transaction_count=tx_count,
            discrepancy_amount=total_discrepancy,
            notes=notes,
        )

    def reconcile_all_providers(
        self,
        report_date: date,
        provider_settlements: dict[str, str],  # provider_name → csv_content
    ) -> list[ReconciliationRecord]:
        """Reconcile multiple PSPs in one pass."""
        results = []
        for provider_name, csv_content in provider_settlements.items():
            try:
                record = self.reconcile(report_date, provider_name, csv_content)
                results.append(record)
            except Exception as exc:
                logger.exception("Reconciliation failed for %s: %s", provider_name, exc)
        return results

#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 12, Real-Time Cash Flow Management for Online Casinos.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Casino Money Monitor - Daily Financial Reconciliation Engine
==============================================================
Chapter 5 Implementation: Checklist Item #6

Performs end-of-day financial reconciliation across all data sources:

1. Player Wallet Reconciliation
   - Sum of all player balances vs. segregated bank accounts
   - Must match per UKGC LCCP 3.2.2 / MGA directive

2. Payment Provider Reconciliation
   - Casino records vs. PSP settlement reports
   - Match deposits, withdrawals, refunds, chargebacks

3. Game Provider Reconciliation
   - Casino GGR calculations vs. game supplier reports
   - RNG and live dealer providers

4. Bank Statement Reconciliation
   - Expected bank movements vs. actual bank statement
   - Identify unmatched/missing transactions

5. Tax Reconciliation
   - GGR-based tax calculations per jurisdiction
   - POC (Point of Consumption) tax for UK

Output: Reconciliation report with matched/unmatched items and variance analysis.

PCI DSS Compliance Notes:
- Requirement 10.7: Retain reconciliation records >= 1 year
- Requirement 10.5: Secure audit trail, tamper-evident
- No cardholder data in reconciliation records (Req 3.4)

Dependencies:
    pip install pydantic sqlalchemy asyncpg
"""

import logging
from datetime import datetime, timezone, date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field

logger = logging.getLogger("daily_reconciliation")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Maximum acceptable variance before flagging (absolute EUR)
VARIANCE_THRESHOLD_WARNING = Decimal("100.00")
VARIANCE_THRESHOLD_CRITICAL = Decimal("1000.00")

# Tolerance for floating-point rounding in multi-currency
ROUNDING_TOLERANCE = Decimal("0.05")


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

class ReconciliationType(str, Enum):
    PLAYER_WALLET = "player_wallet"
    PAYMENT_PROVIDER = "payment_provider"
    GAME_PROVIDER = "game_provider"
    BANK_STATEMENT = "bank_statement"
    TAX = "tax"
    INTER_ENTITY = "inter_entity"


class MatchStatus(str, Enum):
    MATCHED = "matched"
    PARTIAL_MATCH = "partial_match"
    UNMATCHED_SOURCE = "unmatched_source"     # in our records, not in external
    UNMATCHED_EXTERNAL = "unmatched_external"  # in external, not in our records
    VARIANCE = "variance"                      # matched but amounts differ
    TIMING = "timing"                          # cross-day settlement timing


class TransactionRecord(BaseModel):
    """Internal transaction record from our platform."""
    transaction_id: str
    reference: str
    transaction_type: str     # deposit, withdrawal, bet, win, refund, bonus, adjustment
    amount: Decimal
    currency: str
    timestamp: datetime
    player_id: Optional[str] = None
    provider: Optional[str] = None
    status: str = "completed"
    entity_id: str = ""


class ExternalRecord(BaseModel):
    """Record from external source (PSP, bank, game provider)."""
    external_id: str
    reference: str
    record_type: str
    amount: Decimal
    currency: str
    timestamp: datetime
    source: str              # provider name
    raw_data: dict = {}


class ReconciliationItem(BaseModel):
    """A single matched/unmatched reconciliation item."""
    item_id: str = Field(default_factory=lambda: str(uuid4())[:12])
    match_status: MatchStatus
    internal_record: Optional[TransactionRecord] = None
    external_record: Optional[ExternalRecord] = None
    variance: Decimal = Decimal("0")
    variance_pct: Decimal = Decimal("0")
    notes: str = ""


class ReconciliationSection(BaseModel):
    """One section of the reconciliation (e.g., player wallets, PSP X)."""
    section_type: ReconciliationType
    source_name: str
    currency: str
    recon_date: date

    # Totals
    internal_total: Decimal = Decimal("0")
    external_total: Decimal = Decimal("0")
    variance: Decimal = Decimal("0")
    variance_pct: Decimal = Decimal("0")

    # Counts
    total_items: int = 0
    matched: int = 0
    partial_matched: int = 0
    unmatched_internal: int = 0
    unmatched_external: int = 0
    timing_differences: int = 0

    # Items
    items: list[ReconciliationItem] = []

    # Status
    is_balanced: bool = False
    requires_investigation: bool = False


class DailyReconciliationReport(BaseModel):
    """Complete daily reconciliation report."""
    report_id: str = Field(default_factory=lambda: str(uuid4()))
    recon_date: date
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    reporting_currency: str = "EUR"

    # Sections
    sections: list[ReconciliationSection] = []

    # Summary
    total_internal: Decimal = Decimal("0")
    total_external: Decimal = Decimal("0")
    total_variance: Decimal = Decimal("0")
    all_balanced: bool = False
    sections_requiring_investigation: int = 0

    # Sign-off
    prepared_by: str = "system"
    reviewed_by: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Reconciliation Engine
# ---------------------------------------------------------------------------

class ReconciliationEngine:
    """
    Core reconciliation engine that matches internal records against
    external data sources and produces variance reports.
    """

    def __init__(self, tolerance: Decimal = ROUNDING_TOLERANCE):
        self.tolerance = tolerance

    def reconcile_transactions(
        self,
        internal: list[TransactionRecord],
        external: list[ExternalRecord],
        match_key: str = "reference",
    ) -> list[ReconciliationItem]:
        """
        Match internal records against external records.

        Matching strategy:
        1. Exact match on reference ID + amount
        2. Fuzzy match on reference ID (partial match if amounts differ)
        3. Amount-based matching for unmatched items
        4. Remaining items flagged as unmatched
        """
        items: list[ReconciliationItem] = []

        # Index external records by reference
        ext_by_ref: dict[str, ExternalRecord] = {}
        ext_used: set[str] = set()

        for rec in external:
            ext_by_ref[rec.reference] = rec

        # Phase 1: Match by reference
        for int_rec in internal:
            ref = int_rec.reference
            if ref in ext_by_ref and ref not in ext_used:
                ext_rec = ext_by_ref[ref]
                ext_used.add(ref)

                variance = int_rec.amount - ext_rec.amount
                abs_variance = abs(variance)

                if abs_variance <= self.tolerance:
                    items.append(ReconciliationItem(
                        match_status=MatchStatus.MATCHED,
                        internal_record=int_rec,
                        external_record=ext_rec,
                        variance=Decimal("0"),
                    ))
                else:
                    variance_pct = (abs_variance / abs(int_rec.amount) * 100).quantize(
                        Decimal("0.01")
                    ) if int_rec.amount != 0 else Decimal("0")

                    items.append(ReconciliationItem(
                        match_status=MatchStatus.VARIANCE if abs_variance > Decimal("1.00") else MatchStatus.PARTIAL_MATCH,
                        internal_record=int_rec,
                        external_record=ext_rec,
                        variance=variance,
                        variance_pct=variance_pct,
                        notes=f"Amount variance: {variance:+,.2f} ({variance_pct}%)",
                    ))
            else:
                # Check for timing (transaction near midnight might appear next day)
                is_timing = self._is_timing_difference(int_rec)
                items.append(ReconciliationItem(
                    match_status=MatchStatus.TIMING if is_timing else MatchStatus.UNMATCHED_SOURCE,
                    internal_record=int_rec,
                    variance=int_rec.amount,
                    notes="Likely timing difference (near midnight)" if is_timing else "No matching external record",
                ))

        # Phase 2: Unmatched external records
        for ext_rec in external:
            if ext_rec.reference not in ext_used:
                items.append(ReconciliationItem(
                    match_status=MatchStatus.UNMATCHED_EXTERNAL,
                    external_record=ext_rec,
                    variance=-ext_rec.amount,
                    notes="No matching internal record",
                ))

        return items

    def _is_timing_difference(self, record: TransactionRecord) -> bool:
        """Check if a transaction is near midnight (possible cross-day settlement)."""
        hour = record.timestamp.hour
        return hour >= 23 or hour <= 1

    def reconcile_balances(
        self,
        internal_balance: Decimal,
        external_balance: Decimal,
        source: str,
    ) -> ReconciliationItem:
        """Simple balance-to-balance reconciliation."""
        variance = internal_balance - external_balance
        abs_var = abs(variance)

        if abs_var <= self.tolerance:
            status = MatchStatus.MATCHED
        elif abs_var <= VARIANCE_THRESHOLD_WARNING:
            status = MatchStatus.PARTIAL_MATCH
        else:
            status = MatchStatus.VARIANCE

        return ReconciliationItem(
            match_status=status,
            variance=variance,
            notes=f"{source}: internal={internal_balance:,.2f}, external={external_balance:,.2f}, diff={variance:+,.2f}",
        )


# ---------------------------------------------------------------------------
# Daily Reconciliation Runner
# ---------------------------------------------------------------------------

class DailyReconciliationRunner:
    """
    Orchestrates the full daily reconciliation process.
    Typically runs at 03:00 UTC after all settlement batches complete.
    """

    def __init__(self):
        self.engine = ReconciliationEngine()

    async def run_full_reconciliation(self, recon_date: date) -> DailyReconciliationReport:
        """Run all reconciliation checks for a given date."""
        report = DailyReconciliationReport(recon_date=recon_date)

        # 1. Player Wallet Reconciliation
        section = await self._reconcile_player_wallets(recon_date)
        report.sections.append(section)

        # 2. Payment Provider Reconciliation (per PSP)
        for psp in ["Stripe", "Trustly", "PayPal", "Skrill", "Neteller"]:
            section = await self._reconcile_psp(recon_date, psp)
            report.sections.append(section)

        # 3. Game Provider Reconciliation
        for provider in ["Evolution", "NetEnt", "Pragmatic Play", "Playtech"]:
            section = await self._reconcile_game_provider(recon_date, provider)
            report.sections.append(section)

        # 4. Bank Statement Reconciliation
        for bank in ["Barclays GBP", "BOV EUR"]:
            section = await self._reconcile_bank(recon_date, bank)
            report.sections.append(section)

        # 5. Tax Reconciliation
        section = await self._reconcile_tax(recon_date)
        report.sections.append(section)

        # Aggregate
        report.total_internal = sum(s.internal_total for s in report.sections)  # ty:ignore[invalid-assignment]
        report.total_external = sum(s.external_total for s in report.sections)  # ty:ignore[invalid-assignment]
        report.total_variance = sum(s.variance for s in report.sections)  # ty:ignore[invalid-assignment]
        report.all_balanced = all(s.is_balanced for s in report.sections)
        report.sections_requiring_investigation = sum(1 for s in report.sections if s.requires_investigation)

        logger.info(
            f"Reconciliation complete for {recon_date}: "
            f"variance={report.total_variance:+,.2f} EUR, "
            f"balanced={report.all_balanced}, "
            f"investigations={report.sections_requiring_investigation}"
        )

        return report

    async def _reconcile_player_wallets(self, recon_date: date) -> ReconciliationSection:
        """
        Verify total player balances match segregated bank accounts.
        UKGC LCCP 3.2.2: Player funds must be held in segregated accounts.
        """
        section = ReconciliationSection(
            section_type=ReconciliationType.PLAYER_WALLET,
            source_name="Player Wallets vs Segregated Accounts",
            currency="EUR",
            recon_date=recon_date,
        )

        # In production: query player_wallets table and bank_accounts table
        # Demo data
        player_total = Decimal("8547231.45")     # sum of all player wallets
        segregated_total = Decimal("8547189.20")  # sum of segregated bank accounts

        item = self.engine.reconcile_balances(player_total, segregated_total, "Player Funds")
        section.items.append(item)

        section.internal_total = player_total
        section.external_total = segregated_total
        section.variance = player_total - segregated_total
        section.total_items = 1
        section.matched = 1 if item.match_status == MatchStatus.MATCHED else 0

        # Variance of 42.25 is within warning threshold
        section.is_balanced = abs(section.variance) <= VARIANCE_THRESHOLD_WARNING
        section.requires_investigation = abs(section.variance) > VARIANCE_THRESHOLD_WARNING

        return section

    async def _reconcile_psp(self, recon_date: date, psp_name: str) -> ReconciliationSection:
        """Reconcile transactions with a payment service provider."""
        section = ReconciliationSection(
            section_type=ReconciliationType.PAYMENT_PROVIDER,
            source_name=psp_name,
            currency="EUR",
            recon_date=recon_date,
        )

        # Demo: generate realistic transaction data
        base_time = datetime(recon_date.year, recon_date.month, recon_date.day, tzinfo=timezone.utc)

        internal_records = [
            TransactionRecord(transaction_id=f"TXN-{psp_name}-001", reference=f"REF-{psp_name}-D001",
                              transaction_type="deposit", amount=Decimal("100.00"), currency="EUR",
                              timestamp=base_time + timedelta(hours=10, minutes=15),
                              player_id="P-1001", provider=psp_name),
            TransactionRecord(transaction_id=f"TXN-{psp_name}-002", reference=f"REF-{psp_name}-D002",
                              transaction_type="deposit", amount=Decimal("500.00"), currency="EUR",
                              timestamp=base_time + timedelta(hours=11, minutes=30),
                              player_id="P-1002", provider=psp_name),
            TransactionRecord(transaction_id=f"TXN-{psp_name}-003", reference=f"REF-{psp_name}-W001",
                              transaction_type="withdrawal", amount=Decimal("-250.00"), currency="EUR",
                              timestamp=base_time + timedelta(hours=14, minutes=0),
                              player_id="P-1003", provider=psp_name),
            TransactionRecord(transaction_id=f"TXN-{psp_name}-004", reference=f"REF-{psp_name}-D003",
                              transaction_type="deposit", amount=Decimal("75.00"), currency="EUR",
                              timestamp=base_time + timedelta(hours=23, minutes=58),
                              player_id="P-1004", provider=psp_name),
        ]

        external_records = [
            ExternalRecord(external_id=f"EXT-{psp_name}-001", reference=f"REF-{psp_name}-D001",
                           record_type="payment", amount=Decimal("100.00"), currency="EUR",
                           timestamp=base_time + timedelta(hours=10, minutes=15), source=psp_name),
            ExternalRecord(external_id=f"EXT-{psp_name}-002", reference=f"REF-{psp_name}-D002",
                           record_type="payment", amount=Decimal("499.50"), currency="EUR",  # 0.50 variance (fee?)
                           timestamp=base_time + timedelta(hours=11, minutes=30), source=psp_name),
            ExternalRecord(external_id=f"EXT-{psp_name}-003", reference=f"REF-{psp_name}-W001",
                           record_type="payout", amount=Decimal("-250.00"), currency="EUR",
                           timestamp=base_time + timedelta(hours=14, minutes=5), source=psp_name),
            # REF-D003 missing from external (timing: 23:58 transaction may settle next day)
            # Extra record in external (possibly previous day late settlement)
            ExternalRecord(external_id=f"EXT-{psp_name}-004", reference=f"REF-{psp_name}-PREV-W099",
                           record_type="payout", amount=Decimal("-180.00"), currency="EUR",
                           timestamp=base_time + timedelta(hours=2, minutes=0), source=psp_name),
        ]

        items = self.engine.reconcile_transactions(internal_records, external_records)
        section.items = items

        section.internal_total = sum(r.amount for r in internal_records)  # ty:ignore[invalid-assignment]
        section.external_total = sum(r.amount for r in external_records)  # ty:ignore[invalid-assignment]
        section.variance = section.internal_total - section.external_total
        section.total_items = len(items)
        section.matched = sum(1 for i in items if i.match_status == MatchStatus.MATCHED)
        section.partial_matched = sum(1 for i in items if i.match_status == MatchStatus.PARTIAL_MATCH)
        section.unmatched_internal = sum(1 for i in items if i.match_status == MatchStatus.UNMATCHED_SOURCE)
        section.unmatched_external = sum(1 for i in items if i.match_status == MatchStatus.UNMATCHED_EXTERNAL)
        section.timing_differences = sum(1 for i in items if i.match_status == MatchStatus.TIMING)
        section.is_balanced = abs(section.variance) <= VARIANCE_THRESHOLD_WARNING
        section.requires_investigation = section.unmatched_internal > 0 or abs(section.variance) > VARIANCE_THRESHOLD_CRITICAL

        return section

    async def _reconcile_game_provider(self, recon_date: date, provider_name: str) -> ReconciliationSection:
        """Reconcile GGR with game provider reports."""
        section = ReconciliationSection(
            section_type=ReconciliationType.GAME_PROVIDER,
            source_name=provider_name,
            currency="EUR",
            recon_date=recon_date,
        )

        # Demo: our GGR calculation vs provider's report
        our_ggr = Decimal("45230.50")
        provider_ggr = Decimal("45230.50")   # typically exact match for RNG providers

        item = self.engine.reconcile_balances(our_ggr, provider_ggr, f"{provider_name} GGR")
        section.items.append(item)

        section.internal_total = our_ggr
        section.external_total = provider_ggr
        section.variance = our_ggr - provider_ggr
        section.total_items = 1
        section.matched = 1 if item.match_status == MatchStatus.MATCHED else 0
        section.is_balanced = abs(section.variance) <= self.engine.tolerance

        return section

    async def _reconcile_bank(self, recon_date: date, bank_name: str) -> ReconciliationSection:
        """Reconcile expected bank movements vs actual statement."""
        section = ReconciliationSection(
            section_type=ReconciliationType.BANK_STATEMENT,
            source_name=bank_name,
            currency="GBP" if "GBP" in bank_name else "EUR",
            recon_date=recon_date,
        )

        expected_balance = Decimal("4250000.00")
        actual_balance = Decimal("4249875.50")  # small difference due to fees

        item = self.engine.reconcile_balances(expected_balance, actual_balance, bank_name)
        section.items.append(item)

        section.internal_total = expected_balance
        section.external_total = actual_balance
        section.variance = expected_balance - actual_balance
        section.total_items = 1
        section.matched = 1 if item.match_status == MatchStatus.MATCHED else 0
        section.is_balanced = abs(section.variance) <= VARIANCE_THRESHOLD_WARNING
        section.requires_investigation = abs(section.variance) > VARIANCE_THRESHOLD_WARNING

        return section

    async def _reconcile_tax(self, recon_date: date) -> ReconciliationSection:
        """Reconcile tax calculations across jurisdictions."""
        section = ReconciliationSection(
            section_type=ReconciliationType.TAX,
            source_name="Tax Provisions",
            currency="EUR",
            recon_date=recon_date,
        )

        # Demo: calculated tax vs accrued provisions
        # UK POC Tax: 21% of GGR
        # Malta Gaming Tax: 5% of GGR
        calculated_tax = Decimal("28450.00")
        accrued_provision = Decimal("28450.00")

        item = self.engine.reconcile_balances(calculated_tax, accrued_provision, "Tax Provisions")
        section.items.append(item)

        section.internal_total = calculated_tax
        section.external_total = accrued_provision
        section.variance = calculated_tax - accrued_provision
        section.is_balanced = abs(section.variance) <= self.engine.tolerance

        return section


# ---------------------------------------------------------------------------
# Report Formatter
# ---------------------------------------------------------------------------

def print_report(report: DailyReconciliationReport):
    """Print a formatted reconciliation report."""
    print(f"\n{'='*70}")
    print(f"DAILY RECONCILIATION REPORT")
    print(f"Date: {report.recon_date}  |  Generated: {report.generated_at.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Report ID: {report.report_id}")
    print(f"{'='*70}")

    for section in report.sections:
        status_icon = "OK" if section.is_balanced else "INVESTIGATE" if section.requires_investigation else "WARN"
        print(f"\n[{status_icon:11s}] {section.source_name} ({section.currency})")
        print(f"  {'Internal:':20s} {section.internal_total:>14,.2f}")
        print(f"  {'External:':20s} {section.external_total:>14,.2f}")
        print(f"  {'Variance:':20s} {section.variance:>+14,.2f}")

        if section.total_items > 1 or section.unmatched_internal > 0 or section.unmatched_external > 0:
            print(f"  Matched: {section.matched}  |  Partial: {section.partial_matched}  "
                  f"|  Unmatched(int): {section.unmatched_internal}  |  Unmatched(ext): {section.unmatched_external}  "
                  f"|  Timing: {section.timing_differences}")

        for item in section.items:
            if item.match_status not in (MatchStatus.MATCHED,):
                print(f"    -> [{item.match_status.value}] {item.notes}")

    print(f"\n{'='*70}")
    print(f"SUMMARY")
    print(f"  Total Variance:     {report.total_variance:>+14,.2f} {report.reporting_currency}")
    print(f"  All Balanced:       {report.all_balanced}")
    print(f"  Investigations:     {report.sections_requiring_investigation}")
    print(f"{'='*70}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    runner = DailyReconciliationRunner()
    yesterday = date.today() - timedelta(days=1)
    report = await runner.run_full_reconciliation(yesterday)
    print_report(report)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

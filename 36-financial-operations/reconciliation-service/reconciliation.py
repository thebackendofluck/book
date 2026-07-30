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
Reconciliation Service -- Core Engine

Operator-facing reconciliation service with:
  - Job execution (compare two sources of truth)
  - Mismatch detection and severity classification
  - Investigation workflow (assign, note, resolve)
  - Closure approval for audit sign-off
  - Scheduler for automated daily/weekly runs

Design: all state is in-memory for the book's demo. In production,
back this with PostgreSQL and a proper job scheduler (Celery, Temporal).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from adapters import BankAdapter, LedgerAdapter, PSPAdapter, TaxAdapter, WalletAdapter
from models import (
    ClosureStatus,
    JobStatus,
    Mismatch,
    MismatchSeverity,
    MismatchStatus,
    ReconciliationClosure,
    ReconciliationJob,
    ReconciliationType,
    ScheduleConfig,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# External system stubs
# ---------------------------------------------------------------------------


class LedgerClient:
    """
    Stub for querying the ledger service.
    In production, calls the Ledger Service REST API.
    """

    def __init__(self) -> None:
        self._balances: dict[str, int] = {}

    def set_balance(self, account_id: str, balance: int) -> None:
        self._balances[account_id] = balance

    def get_balance(self, account_id: str) -> int:
        return self._balances.get(account_id, 0)


class WalletClient:
    """Stub for the wallet microservice."""

    def __init__(self) -> None:
        self._balances: dict[str, int] = {}

    def set_balance(self, player_id: str, balance: int) -> None:
        self._balances[player_id] = balance

    def get_balance(self, player_id: str) -> int:
        return self._balances.get(player_id, 0)

    def list_player_ids(self) -> list[str]:
        return list(self._balances.keys())


class PSPClient:
    """Stub for PSP settlement reporting."""

    def __init__(self) -> None:
        self._positions: dict[str, int] = {}

    def set_position(self, psp_name: str, balance: int) -> None:
        self._positions[psp_name] = balance

    def get_position(self, psp_name: str) -> int:
        return self._positions.get(psp_name, 0)

    def list_psp_names(self) -> list[str]:
        return list(self._positions.keys())


class BankClient:
    """Stub for bank statement feed."""

    def __init__(self) -> None:
        self._balances: dict[str, int] = {}

    def set_balance(self, account_name: str, balance: int) -> None:
        self._balances[account_name] = balance

    def get_balance(self, account_name: str) -> int:
        return self._balances.get(account_name, 0)

    def list_accounts(self) -> list[str]:
        return list(self._balances.keys())


class TaxClient:
    """Stub for tax authority reporting."""

    def __init__(self) -> None:
        self._liabilities: dict[str, int] = {}

    def set_liability(self, jurisdiction: str, amount: int) -> None:
        self._liabilities[jurisdiction] = amount

    def get_liability(self, jurisdiction: str) -> int:
        return self._liabilities.get(jurisdiction, 0)

    def list_jurisdictions(self) -> list[str]:
        return list(self._liabilities.keys())


# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------


class ReconciliationStore:
    """In-memory store for reconciliation state."""

    def __init__(self) -> None:
        self._jobs: dict[str, ReconciliationJob] = {}
        self._mismatches: dict[str, Mismatch] = {}
        self._closures: dict[str, ReconciliationClosure] = {}
        self._schedules: list[ScheduleConfig] = []

    # Jobs
    def save_job(self, job: ReconciliationJob) -> ReconciliationJob:
        self._jobs[job.job_id] = job
        return job

    def get_job(self, job_id: str) -> Optional[ReconciliationJob]:
        return self._jobs.get(job_id)

    def list_jobs(self) -> list[ReconciliationJob]:
        return list(self._jobs.values())

    # Mismatches
    def save_mismatch(self, mismatch: Mismatch) -> Mismatch:
        self._mismatches[mismatch.mismatch_id] = mismatch
        return mismatch

    def get_mismatch(self, mismatch_id: str) -> Optional[Mismatch]:
        return self._mismatches.get(mismatch_id)

    def list_mismatches(
        self, status: Optional[MismatchStatus] = None
    ) -> list[Mismatch]:
        if status:
            return [m for m in self._mismatches.values() if m.status == status]
        return list(self._mismatches.values())

    def list_open_mismatches(self) -> list[Mismatch]:
        return [
            m for m in self._mismatches.values()
            if m.status in {MismatchStatus.OPEN, MismatchStatus.INVESTIGATING}
        ]

    # Closures
    def save_closure(self, closure: ReconciliationClosure) -> ReconciliationClosure:
        self._closures[closure.closure_id] = closure
        return closure

    def get_closure(self, closure_id: str) -> Optional[ReconciliationClosure]:
        return self._closures.get(closure_id)

    def get_closure_by_period(self, period_label: str) -> Optional[ReconciliationClosure]:
        for c in self._closures.values():
            if c.period_label == period_label:
                return c
        return None

    def list_closures(self) -> list[ReconciliationClosure]:
        return list(self._closures.values())

    # Schedules
    def add_schedule(self, config: ScheduleConfig) -> None:
        self._schedules.append(config)

    def list_schedules(self) -> list[ScheduleConfig]:
        return list(self._schedules)


# ---------------------------------------------------------------------------
# Severity classifier
# ---------------------------------------------------------------------------


def classify_severity(discrepancy: int, source_balance: int) -> MismatchSeverity:
    """
    Classify mismatch severity based on absolute amount and percentage.

    Rules:
      - < 100 minor units => INFO (likely rounding / timing)
      - >= 100 and < 10000, or < 1% of source => WARNING
      - >= 10000 or >= 1% of source => CRITICAL
    """
    if discrepancy < 100:
        return MismatchSeverity.INFO

    if source_balance > 0:
        pct = discrepancy / source_balance
        if pct >= 0.01:
            return MismatchSeverity.CRITICAL

    if discrepancy >= 10_000:
        return MismatchSeverity.CRITICAL

    return MismatchSeverity.WARNING


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ReconciliationService:
    """
    Orchestrates reconciliation jobs, mismatch tracking, and period closure.
    """

    def __init__(
        self,
        store: ReconciliationStore | None = None,
        ledger: LedgerClient | LedgerAdapter | None = None,
        wallet: WalletClient | WalletAdapter | None = None,
        psp: PSPClient | PSPAdapter | None = None,
        bank: BankClient | BankAdapter | None = None,
        tax: TaxClient | TaxAdapter | None = None,
    ) -> None:
        self._store = store or ReconciliationStore()
        self._ledger = ledger or LedgerClient()
        self._wallet = wallet or WalletClient()
        self._psp = psp or PSPClient()
        self._bank = bank or BankClient()
        self._tax = tax or TaxClient()

    # ------------------------------------------------------------------
    # Job execution
    # ------------------------------------------------------------------

    def _execute_comparison(
        self,
        job: ReconciliationJob,
        source_a_balance: int,
        source_b_balance: int,
        source_a_label: str,
        source_b_label: str,
    ) -> ReconciliationJob:
        """Run the comparison and update the job."""
        discrepancy = abs(source_a_balance - source_b_balance)
        is_matched = discrepancy == 0

        updated = job.model_copy(update={
            "status": JobStatus.COMPLETED,
            "source_a_label": source_a_label,
            "source_a_balance": source_a_balance,
            "source_b_label": source_b_label,
            "source_b_balance": source_b_balance,
            "discrepancy": discrepancy,
            "is_matched": is_matched,
            "completed_at": datetime.now(timezone.utc),
        })
        self._store.save_job(updated)

        if not is_matched:
            severity = classify_severity(discrepancy, max(source_a_balance, source_b_balance, 1))
            mismatch = Mismatch(
                job_id=updated.job_id,
                recon_type=updated.recon_type,
                entity_id=updated.entity_id,
                source_a_label=source_a_label,
                source_a_balance=source_a_balance,
                source_b_label=source_b_label,
                source_b_balance=source_b_balance,
                discrepancy=discrepancy,
                severity=severity,
            )
            self._store.save_mismatch(mismatch)
            logger.warning(
                "Mismatch detected: %s entity=%s discrepancy=%d severity=%s",
                updated.recon_type.value, updated.entity_id, discrepancy, severity.value,
            )

        return updated

    def reconcile_wallet(self, player_id: str) -> ReconciliationJob:
        """Compare ledger vs wallet for a single player."""
        job = ReconciliationJob(
            recon_type=ReconciliationType.LEDGER_VS_WALLET,
            entity_id=player_id,
            status=JobStatus.RUNNING,
            started_at=datetime.now(timezone.utc),
        )
        self._store.save_job(job)

        ledger_bal = self._ledger.get_balance(f"PLAYER_WALLET:{player_id}")
        wallet_bal = self._wallet.get_balance(player_id)

        return self._execute_comparison(
            job, ledger_bal, wallet_bal,
            f"ledger:PLAYER_WALLET:{player_id}",
            f"wallet:{player_id}",
        )

    def reconcile_psp(self, psp_name: str) -> ReconciliationJob:
        """Compare ledger PSP clearing vs PSP reported position."""
        job = ReconciliationJob(
            recon_type=ReconciliationType.LEDGER_VS_PSP,
            entity_id=psp_name,
            status=JobStatus.RUNNING,
            started_at=datetime.now(timezone.utc),
        )
        self._store.save_job(job)

        ledger_bal = self._ledger.get_balance(f"PSP_CLEARING:{psp_name}")
        psp_bal = self._psp.get_position(psp_name)

        return self._execute_comparison(
            job, ledger_bal, psp_bal,
            f"ledger:PSP_CLEARING:{psp_name}",
            f"psp_report:{psp_name}",
        )

    def reconcile_bank(self, account_name: str) -> ReconciliationJob:
        """Compare ledger bank settlement vs bank statement."""
        job = ReconciliationJob(
            recon_type=ReconciliationType.LEDGER_VS_BANK,
            entity_id=account_name,
            status=JobStatus.RUNNING,
            started_at=datetime.now(timezone.utc),
        )
        self._store.save_job(job)

        ledger_bal = self._ledger.get_balance(f"BANK_SETTLEMENT:{account_name}")
        bank_bal = self._bank.get_balance(account_name)

        return self._execute_comparison(
            job, ledger_bal, bank_bal,
            f"ledger:BANK_SETTLEMENT:{account_name}",
            f"bank_statement:{account_name}",
        )

    def reconcile_tax(self, jurisdiction: str) -> ReconciliationJob:
        """Compare ledger tax liability vs tax authority expected amount."""
        job = ReconciliationJob(
            recon_type=ReconciliationType.LEDGER_VS_TAX,
            entity_id=jurisdiction,
            status=JobStatus.RUNNING,
            started_at=datetime.now(timezone.utc),
        )
        self._store.save_job(job)

        ledger_bal = self._ledger.get_balance(f"TAX_LIABILITY:{jurisdiction}")
        tax_bal = self._tax.get_liability(jurisdiction)

        return self._execute_comparison(
            job, ledger_bal, tax_bal,
            f"ledger:TAX_LIABILITY:{jurisdiction}",
            f"tax_authority:{jurisdiction}",
        )

    # ------------------------------------------------------------------
    # Daily auto-reconciliation
    # ------------------------------------------------------------------

    def run_daily_reconciliation(self) -> list[ReconciliationJob]:
        """
        Execute reconciliation for all known entities.

        In production this would be triggered by a cron/scheduler.
        Returns all completed jobs for the run.
        """
        jobs: list[ReconciliationJob] = []

        # Wallet reconciliation
        for player_id in self._wallet.list_player_ids():
            try:
                jobs.append(self.reconcile_wallet(player_id))
            except Exception as exc:
                logger.error("Wallet recon failed for %s: %s", player_id, exc)

        # PSP reconciliation
        for psp_name in self._psp.list_psp_names():
            try:
                jobs.append(self.reconcile_psp(psp_name))
            except Exception as exc:
                logger.error("PSP recon failed for %s: %s", psp_name, exc)

        # Bank reconciliation
        for account in self._bank.list_accounts():
            try:
                jobs.append(self.reconcile_bank(account))
            except Exception as exc:
                logger.error("Bank recon failed for %s: %s", account, exc)

        # Tax reconciliation
        for jurisdiction in self._tax.list_jurisdictions():
            try:
                jobs.append(self.reconcile_tax(jurisdiction))
            except Exception as exc:
                logger.error("Tax recon failed for %s: %s", jurisdiction, exc)

        logger.info(
            "Daily reconciliation complete: %d jobs, %d matched, %d mismatched",
            len(jobs),
            sum(1 for j in jobs if j.is_matched),
            sum(1 for j in jobs if not j.is_matched),
        )
        return jobs

    # ------------------------------------------------------------------
    # Mismatch investigation workflow
    # ------------------------------------------------------------------

    def assign_mismatch(self, mismatch_id: str, assignee: str) -> Mismatch:
        """Assign a mismatch to an investigator."""
        m = self._store.get_mismatch(mismatch_id)
        if m is None:
            raise ValueError(f"Mismatch not found: {mismatch_id}")

        updated = m.model_copy(update={
            "status": MismatchStatus.INVESTIGATING,
            "assigned_to": assignee,
        })
        return self._store.save_mismatch(updated)

    def add_investigation_note(self, mismatch_id: str, note: str) -> Mismatch:
        """Add a note to an ongoing investigation."""
        m = self._store.get_mismatch(mismatch_id)
        if m is None:
            raise ValueError(f"Mismatch not found: {mismatch_id}")

        notes = list(m.investigation_notes)
        timestamp = datetime.now(timezone.utc).isoformat()
        notes.append(f"[{timestamp}] {note}")

        updated = m.model_copy(update={"investigation_notes": notes})
        return self._store.save_mismatch(updated)

    def resolve_mismatch(
        self,
        mismatch_id: str,
        resolution: str,
        resolved_by: str,
    ) -> Mismatch:
        """Mark a mismatch as resolved."""
        m = self._store.get_mismatch(mismatch_id)
        if m is None:
            raise ValueError(f"Mismatch not found: {mismatch_id}")

        updated = m.model_copy(update={
            "status": MismatchStatus.RESOLVED,
            "resolution": resolution,
            "resolved_by": resolved_by,
            "resolved_at": datetime.now(timezone.utc),
        })
        self._store.save_mismatch(updated)

        logger.info(
            "Mismatch %s resolved by %s: %s",
            mismatch_id, resolved_by, resolution,
        )
        return updated

    def waive_mismatch(
        self,
        mismatch_id: str,
        reason: str,
        approved_by: str,
    ) -> Mismatch:
        """
        Waive (write off) a mismatch. Requires explicit approval.

        This is an auditable action -- regulators will review waived
        mismatches during inspections.
        """
        m = self._store.get_mismatch(mismatch_id)
        if m is None:
            raise ValueError(f"Mismatch not found: {mismatch_id}")

        updated = m.model_copy(update={
            "status": MismatchStatus.WAIVED,
            "resolution": f"WAIVED: {reason}",
            "resolved_by": approved_by,
            "resolved_at": datetime.now(timezone.utc),
        })
        self._store.save_mismatch(updated)

        logger.warning(
            "Mismatch %s WAIVED by %s: %s (amount=%d)",
            mismatch_id, approved_by, reason, m.discrepancy,
        )
        return updated

    # ------------------------------------------------------------------
    # Closure approval
    # ------------------------------------------------------------------

    def create_closure(self, period_label: str) -> ReconciliationClosure:
        """
        Create a closure record for a reconciliation period.

        Collects all jobs and their mismatch status.
        """
        existing = self._store.get_closure_by_period(period_label)
        if existing:
            return existing

        jobs = self._store.list_jobs()
        job_ids = [j.job_id for j in jobs]
        matched = sum(1 for j in jobs if j.is_matched)
        mismatched = sum(1 for j in jobs if not j.is_matched)
        open_mismatches = len(self._store.list_open_mismatches())

        status = ClosureStatus.READY if open_mismatches == 0 else ClosureStatus.PENDING

        closure = ReconciliationClosure(
            period_label=period_label,
            status=status,
            job_ids=job_ids,
            total_jobs=len(jobs),
            total_matched=matched,
            total_mismatched=mismatched,
            open_mismatches=open_mismatches,
        )
        return self._store.save_closure(closure)

    def approve_closure(
        self,
        closure_id: str,
        approved_by: str,
    ) -> ReconciliationClosure:
        """
        Approve a reconciliation closure.

        Only possible when all mismatches are resolved or waived.
        This is the finance controller's sign-off that satisfies
        NJ DGE / MGA audit requirements.
        """
        c = self._store.get_closure(closure_id)
        if c is None:
            raise ValueError(f"Closure not found: {closure_id}")

        # Recompute open mismatches
        open_mismatches = len(self._store.list_open_mismatches())
        if open_mismatches > 0:
            raise ValueError(
                f"Cannot approve closure: {open_mismatches} open mismatch(es) remain"
            )

        updated = c.model_copy(update={
            "status": ClosureStatus.APPROVED,
            "open_mismatches": 0,
            "approved_by": approved_by,
            "approved_at": datetime.now(timezone.utc),
        })
        self._store.save_closure(updated)

        logger.info("Closure %s approved by %s", closure_id, approved_by)
        return updated

    def reject_closure(
        self,
        closure_id: str,
        reason: str,
    ) -> ReconciliationClosure:
        """Reject a closure, sending it back for further investigation."""
        c = self._store.get_closure(closure_id)
        if c is None:
            raise ValueError(f"Closure not found: {closure_id}")

        updated = c.model_copy(update={
            "status": ClosureStatus.REJECTED,
            "rejection_reason": reason,
        })
        self._store.save_closure(updated)

        logger.warning("Closure %s rejected: %s", closure_id, reason)
        return updated

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_open_mismatches(self) -> list[Mismatch]:
        """Return all unresolved mismatches."""
        return self._store.list_open_mismatches()

    def get_mismatches_by_severity(
        self, severity: MismatchSeverity
    ) -> list[Mismatch]:
        """Return mismatches filtered by severity."""
        return [
            m for m in self._store.list_mismatches()
            if m.severity == severity
        ]

    def get_job_summary(self) -> dict:
        """Return a summary of all reconciliation jobs."""
        jobs = self._store.list_jobs()
        return {
            "total": len(jobs),
            "completed": sum(1 for j in jobs if j.status == JobStatus.COMPLETED),
            "matched": sum(1 for j in jobs if j.is_matched),
            "mismatched": sum(1 for j in jobs if not j.is_matched),
            "failed": sum(1 for j in jobs if j.status == JobStatus.FAILED),
        }

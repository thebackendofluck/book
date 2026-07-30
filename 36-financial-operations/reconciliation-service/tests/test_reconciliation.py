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
Reconciliation Service -- Test Suite

32 tests covering:
  1.  Severity classification
  2.  Wallet reconciliation (match and mismatch)
  3.  PSP reconciliation (match and mismatch)
  4.  Bank reconciliation (match and mismatch)
  5.  Tax reconciliation (match and mismatch)
  6.  Daily auto-reconciliation
  7.  Mismatch investigation workflow
  8.  Closure approval workflow
  9.  Edge cases
"""

from __future__ import annotations

import importlib.util
import sys
import os
from pathlib import Path
from importlib.machinery import ModuleSpec
from typing import cast

import pytest

SERVICE_DIR = Path(__file__).resolve().parent.parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))


def _load_local_module(module_name: str, file_name: str):
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(module_name, SERVICE_DIR / file_name)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(cast(ModuleSpec, spec))
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_load_local_module("models", "models.py")
_load_local_module("reconciliation", "reconciliation.py")

from models import (
    ClosureStatus,
    JobStatus,
    MismatchSeverity,
    MismatchStatus,
    ReconciliationType,
)
from reconciliation import (
    BankClient,
    LedgerClient,
    PSPClient,
    ReconciliationService,
    ReconciliationStore,
    TaxClient,
    WalletClient,
    classify_severity,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_service(
    ledger_balances: dict[str, int] | None = None,
    wallet_balances: dict[str, int] | None = None,
    psp_positions: dict[str, int] | None = None,
    bank_balances: dict[str, int] | None = None,
    tax_liabilities: dict[str, int] | None = None,
) -> ReconciliationService:
    """Build a service with seeded external system stubs."""
    ledger = LedgerClient()
    for k, v in (ledger_balances or {}).items():
        ledger.set_balance(k, v)

    wallet = WalletClient()
    for k, v in (wallet_balances or {}).items():
        wallet.set_balance(k, v)

    psp = PSPClient()
    for k, v in (psp_positions or {}).items():
        psp.set_position(k, v)

    bank = BankClient()
    for k, v in (bank_balances or {}).items():
        bank.set_balance(k, v)

    tax = TaxClient()
    for k, v in (tax_liabilities or {}).items():
        tax.set_liability(k, v)

    return ReconciliationService(
        store=ReconciliationStore(),
        ledger=ledger,
        wallet=wallet,
        psp=psp,
        bank=bank,
        tax=tax,
    )


# ---------------------------------------------------------------------------
# 1. Severity classification
# ---------------------------------------------------------------------------


def test_severity_info_small_amount():
    assert classify_severity(50, 100_000) == MismatchSeverity.INFO


def test_severity_warning_medium_amount():
    assert classify_severity(5_000, 1_000_000) == MismatchSeverity.WARNING


def test_severity_critical_large_amount():
    assert classify_severity(50_000, 1_000_000) == MismatchSeverity.CRITICAL


def test_severity_critical_high_percentage():
    # 200 out of 10000 = 2% -> CRITICAL
    assert classify_severity(200, 10_000) == MismatchSeverity.CRITICAL


def test_severity_zero_source_balance():
    assert classify_severity(5_000, 0) == MismatchSeverity.WARNING


# ---------------------------------------------------------------------------
# 2. Wallet reconciliation
# ---------------------------------------------------------------------------


def test_wallet_recon_match():
    svc = make_service(
        ledger_balances={"PLAYER_WALLET:p1": 10_000},
        wallet_balances={"p1": 10_000},
    )
    job = svc.reconcile_wallet("p1")
    assert job.is_matched is True
    assert job.discrepancy == 0
    assert job.status == JobStatus.COMPLETED


def test_wallet_recon_mismatch():
    svc = make_service(
        ledger_balances={"PLAYER_WALLET:p1": 10_000},
        wallet_balances={"p1": 9_500},
    )
    job = svc.reconcile_wallet("p1")
    assert job.is_matched is False
    assert job.discrepancy == 500


def test_wallet_recon_creates_mismatch_record():
    svc = make_service(
        ledger_balances={"PLAYER_WALLET:p1": 10_000},
        wallet_balances={"p1": 5_000},
    )
    svc.reconcile_wallet("p1")
    mismatches = svc.get_open_mismatches()
    assert len(mismatches) == 1
    assert mismatches[0].recon_type == ReconciliationType.LEDGER_VS_WALLET


# ---------------------------------------------------------------------------
# 3. PSP reconciliation
# ---------------------------------------------------------------------------


def test_psp_recon_match():
    svc = make_service(
        ledger_balances={"PSP_CLEARING:adyen": 50_000},
        psp_positions={"adyen": 50_000},
    )
    job = svc.reconcile_psp("adyen")
    assert job.is_matched is True


def test_psp_recon_mismatch():
    svc = make_service(
        ledger_balances={"PSP_CLEARING:adyen": 50_000},
        psp_positions={"adyen": 45_000},
    )
    job = svc.reconcile_psp("adyen")
    assert job.is_matched is False
    assert job.discrepancy == 5_000


# ---------------------------------------------------------------------------
# 4. Bank reconciliation
# ---------------------------------------------------------------------------


def test_bank_recon_match():
    svc = make_service(
        ledger_balances={"BANK_SETTLEMENT:main_eur": 200_000},
        bank_balances={"main_eur": 200_000},
    )
    job = svc.reconcile_bank("main_eur")
    assert job.is_matched is True


def test_bank_recon_mismatch():
    svc = make_service(
        ledger_balances={"BANK_SETTLEMENT:main_eur": 200_000},
        bank_balances={"main_eur": 195_000},
    )
    job = svc.reconcile_bank("main_eur")
    assert job.is_matched is False
    assert job.discrepancy == 5_000


# ---------------------------------------------------------------------------
# 5. Tax reconciliation
# ---------------------------------------------------------------------------


def test_tax_recon_match():
    svc = make_service(
        ledger_balances={"TAX_LIABILITY:MT": 30_000},
        tax_liabilities={"MT": 30_000},
    )
    job = svc.reconcile_tax("MT")
    assert job.is_matched is True


def test_tax_recon_mismatch():
    svc = make_service(
        ledger_balances={"TAX_LIABILITY:MT": 30_000},
        tax_liabilities={"MT": 28_000},
    )
    job = svc.reconcile_tax("MT")
    assert job.is_matched is False
    assert job.discrepancy == 2_000


# ---------------------------------------------------------------------------
# 6. Daily auto-reconciliation
# ---------------------------------------------------------------------------


def test_daily_recon_runs_all_types():
    svc = make_service(
        ledger_balances={
            "PLAYER_WALLET:p1": 10_000,
            "PLAYER_WALLET:p2": 20_000,
            "PSP_CLEARING:adyen": 50_000,
            "BANK_SETTLEMENT:main": 100_000,
            "TAX_LIABILITY:MT": 15_000,
        },
        wallet_balances={"p1": 10_000, "p2": 20_000},
        psp_positions={"adyen": 50_000},
        bank_balances={"main": 100_000},
        tax_liabilities={"MT": 15_000},
    )
    jobs = svc.run_daily_reconciliation()
    assert len(jobs) == 5  # 2 wallets + 1 PSP + 1 bank + 1 tax
    assert all(j.is_matched for j in jobs)


def test_daily_recon_with_mismatches():
    svc = make_service(
        ledger_balances={"PLAYER_WALLET:p1": 10_000, "PSP_CLEARING:adyen": 50_000},
        wallet_balances={"p1": 9_000},
        psp_positions={"adyen": 48_000},
    )
    jobs = svc.run_daily_reconciliation()
    assert len(jobs) == 2
    assert sum(1 for j in jobs if not j.is_matched) == 2


def test_daily_recon_empty_sources():
    svc = make_service()
    jobs = svc.run_daily_reconciliation()
    assert len(jobs) == 0


# ---------------------------------------------------------------------------
# 7. Mismatch investigation workflow
# ---------------------------------------------------------------------------


def test_assign_mismatch():
    svc = make_service(
        ledger_balances={"PLAYER_WALLET:p1": 10_000},
        wallet_balances={"p1": 5_000},
    )
    svc.reconcile_wallet("p1")
    mismatches = svc.get_open_mismatches()
    m = svc.assign_mismatch(mismatches[0].mismatch_id, "jane@casino.com")
    assert m.status == MismatchStatus.INVESTIGATING
    assert m.assigned_to == "jane@casino.com"


def test_add_investigation_note():
    svc = make_service(
        ledger_balances={"PLAYER_WALLET:p1": 10_000},
        wallet_balances={"p1": 5_000},
    )
    svc.reconcile_wallet("p1")
    m_id = svc.get_open_mismatches()[0].mismatch_id
    m = svc.add_investigation_note(m_id, "Checking wallet DB logs")
    assert len(m.investigation_notes) == 1
    assert "Checking wallet DB logs" in m.investigation_notes[0]


def test_resolve_mismatch():
    svc = make_service(
        ledger_balances={"PLAYER_WALLET:p1": 10_000},
        wallet_balances={"p1": 5_000},
    )
    svc.reconcile_wallet("p1")
    m_id = svc.get_open_mismatches()[0].mismatch_id
    svc.assign_mismatch(m_id, "jane@casino.com")
    m = svc.resolve_mismatch(m_id, "Wallet DB had stale cache", "jane@casino.com")
    assert m.status == MismatchStatus.RESOLVED
    assert m.resolution == "Wallet DB had stale cache"
    assert m.resolved_at is not None


def test_waive_mismatch():
    svc = make_service(
        ledger_balances={"PLAYER_WALLET:p1": 10_000},
        wallet_balances={"p1": 9_950},
    )
    svc.reconcile_wallet("p1")
    m_id = svc.get_open_mismatches()[0].mismatch_id
    m = svc.waive_mismatch(m_id, "Rounding difference < 1 EUR", "cfo@casino.com")
    assert m.status == MismatchStatus.WAIVED
    assert m.resolution is not None
    assert "WAIVED" in m.resolution


def test_resolve_clears_open_list():
    svc = make_service(
        ledger_balances={"PLAYER_WALLET:p1": 10_000},
        wallet_balances={"p1": 5_000},
    )
    svc.reconcile_wallet("p1")
    m_id = svc.get_open_mismatches()[0].mismatch_id
    svc.resolve_mismatch(m_id, "Fixed", "jane@casino.com")
    assert len(svc.get_open_mismatches()) == 0


def test_assign_nonexistent_mismatch_raises():
    svc = make_service()
    with pytest.raises(ValueError, match="not found"):
        svc.assign_mismatch("nonexistent", "jane@casino.com")


# ---------------------------------------------------------------------------
# 8. Closure approval workflow
# ---------------------------------------------------------------------------


def test_create_closure_ready_when_no_mismatches():
    svc = make_service(
        ledger_balances={"PLAYER_WALLET:p1": 10_000},
        wallet_balances={"p1": 10_000},
    )
    svc.reconcile_wallet("p1")
    closure = svc.create_closure("2026-03-23")
    assert closure.status == ClosureStatus.READY
    assert closure.open_mismatches == 0


def test_create_closure_pending_with_open_mismatches():
    svc = make_service(
        ledger_balances={"PLAYER_WALLET:p1": 10_000},
        wallet_balances={"p1": 5_000},
    )
    svc.reconcile_wallet("p1")
    closure = svc.create_closure("2026-03-23")
    assert closure.status == ClosureStatus.PENDING
    assert closure.open_mismatches == 1


def test_approve_closure_after_resolving():
    svc = make_service(
        ledger_balances={"PLAYER_WALLET:p1": 10_000},
        wallet_balances={"p1": 5_000},
    )
    svc.reconcile_wallet("p1")
    m_id = svc.get_open_mismatches()[0].mismatch_id
    svc.resolve_mismatch(m_id, "Fixed", "jane@casino.com")

    closure = svc.create_closure("2026-03-23")
    approved = svc.approve_closure(closure.closure_id, "cfo@casino.com")
    assert approved.status == ClosureStatus.APPROVED
    assert approved.approved_by == "cfo@casino.com"
    assert approved.approved_at is not None


def test_cannot_approve_with_open_mismatches():
    svc = make_service(
        ledger_balances={"PLAYER_WALLET:p1": 10_000},
        wallet_balances={"p1": 5_000},
    )
    svc.reconcile_wallet("p1")
    closure = svc.create_closure("2026-03-23")
    with pytest.raises(ValueError, match="open mismatch"):
        svc.approve_closure(closure.closure_id, "cfo@casino.com")


def test_reject_closure():
    svc = make_service(
        ledger_balances={"PLAYER_WALLET:p1": 10_000},
        wallet_balances={"p1": 10_000},
    )
    svc.reconcile_wallet("p1")
    closure = svc.create_closure("2026-03-23")
    rejected = svc.reject_closure(closure.closure_id, "Need to re-check PSP data")
    assert rejected.status == ClosureStatus.REJECTED
    assert rejected.rejection_reason == "Need to re-check PSP data"


def test_closure_idempotent():
    svc = make_service(
        ledger_balances={"PLAYER_WALLET:p1": 10_000},
        wallet_balances={"p1": 10_000},
    )
    svc.reconcile_wallet("p1")
    c1 = svc.create_closure("2026-03-23")
    c2 = svc.create_closure("2026-03-23")
    assert c1.closure_id == c2.closure_id


# ---------------------------------------------------------------------------
# 9. Edge cases and summary
# ---------------------------------------------------------------------------


def test_job_summary():
    svc = make_service(
        ledger_balances={"PLAYER_WALLET:p1": 10_000, "PLAYER_WALLET:p2": 20_000},
        wallet_balances={"p1": 10_000, "p2": 15_000},
    )
    svc.run_daily_reconciliation()
    summary = svc.get_job_summary()
    assert summary["total"] == 2
    assert summary["matched"] == 1
    assert summary["mismatched"] == 1


def test_mismatches_by_severity():
    svc = make_service(
        ledger_balances={
            "PLAYER_WALLET:p1": 10_000,
            "PLAYER_WALLET:p2": 100_000,
        },
        wallet_balances={
            "p1": 9_950,   # 50 off -> INFO
            "p2": 50_000,  # 50000 off -> CRITICAL
        },
    )
    svc.run_daily_reconciliation()
    critical = svc.get_mismatches_by_severity(MismatchSeverity.CRITICAL)
    info = svc.get_mismatches_by_severity(MismatchSeverity.INFO)
    assert len(critical) == 1
    assert len(info) == 1

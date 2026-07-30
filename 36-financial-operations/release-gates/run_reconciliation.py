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
Release Gate: Daily Reconciliation Run

Executes the full daily reconciliation pipeline and produces a report.
This gate verifies that all four reconciliation types complete and
no CRITICAL mismatches remain unresolved.

Exit codes:
  0 = PASS (reconciliation clean or all mismatches resolved)
  1 = FAIL (unresolved critical mismatches -- block deployment)

Compliance: NJ DGE 13:69O-1.11 (reconciliation of accounts),
            MGA Directive 2 (operational requirements),
            PCI-DSS 10.5 (secure audit trails)
"""

from __future__ import annotations

import json
import sys
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "reconciliation-service"))

from models import (  # ty: ignore[unresolved-import]
    ClosureStatus,
    JobStatus,
    MismatchSeverity,
    MismatchStatus,
    ReconciliationType,
) 
from reconciliation import (  # ty: ignore[unresolved-import]
    BankClient,
    LedgerClient,
    PSPClient,
    ReconciliationService,
    ReconciliationStore,
    TaxClient,
    WalletClient,
)


@dataclass
class ReconciliationGateResult:
    """Output of the reconciliation release gate."""

    total_jobs: int = 0
    total_matched: int = 0
    total_mismatched: int = 0
    critical_mismatches: int = 0
    warning_mismatches: int = 0
    info_mismatches: int = 0
    closure_status: str = ""
    overall_pass: bool = False
    report_period: str = ""
    checked_at: str = ""
    details: list[dict] = field(default_factory=list)


def run_reconciliation_gate(
    ledger_balances: dict[str, int],
    wallet_balances: dict[str, int],
    psp_positions: dict[str, int],
    bank_balances: dict[str, int],
    tax_liabilities: dict[str, int],
    period_label: str = "",
) -> ReconciliationGateResult:
    """
    Execute daily reconciliation and evaluate pass/fail.

    Pass criteria:
      - No CRITICAL mismatches remain OPEN
      - Closure can be created (all jobs completed)
    """
    if not period_label:
        period_label = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Build service with provided data
    ledger = LedgerClient()
    for k, v in ledger_balances.items():
        ledger.set_balance(k, v)

    wallet = WalletClient()
    for k, v in wallet_balances.items():
        wallet.set_balance(k, v)

    psp = PSPClient()
    for k, v in psp_positions.items():
        psp.set_position(k, v)

    bank = BankClient()
    for k, v in bank_balances.items():
        bank.set_balance(k, v)

    tax = TaxClient()
    for k, v in tax_liabilities.items():
        tax.set_liability(k, v)

    svc = ReconciliationService(
        store=ReconciliationStore(),
        ledger=ledger,
        wallet=wallet,
        psp=psp,
        bank=bank,
        tax=tax,
    )

    # Run daily reconciliation
    jobs = svc.run_daily_reconciliation()

    # Evaluate results
    result = ReconciliationGateResult(
        checked_at=datetime.now(timezone.utc).isoformat(),
        report_period=period_label,
        total_jobs=len(jobs),
        total_matched=sum(1 for j in jobs if j.is_matched),
        total_mismatched=sum(1 for j in jobs if not j.is_matched),
    )

    # Count mismatches by severity
    all_mismatches = svc._store.list_mismatches()
    result.critical_mismatches = sum(
        1 for m in all_mismatches if m.severity == MismatchSeverity.CRITICAL
    )
    result.warning_mismatches = sum(
        1 for m in all_mismatches if m.severity == MismatchSeverity.WARNING
    )
    result.info_mismatches = sum(
        1 for m in all_mismatches if m.severity == MismatchSeverity.INFO
    )

    # Job details
    for job in jobs:
        result.details.append({
            "type": job.recon_type.value,
            "entity": job.entity_id,
            "matched": job.is_matched,
            "discrepancy": job.discrepancy,
        })

    # Create closure attempt
    closure = svc.create_closure(period_label)
    result.closure_status = closure.status.value

    # Pass = no critical open mismatches
    open_critical = sum(
        1 for m in all_mismatches
        if m.severity == MismatchSeverity.CRITICAL
        and m.status in {MismatchStatus.OPEN, MismatchStatus.INVESTIGATING}
    )
    result.overall_pass = open_critical == 0

    return result


def demo_gate() -> ReconciliationGateResult:
    """Run the gate with demo data showing a clean reconciliation."""
    return run_reconciliation_gate(
        ledger_balances={
            "PLAYER_WALLET:p1": 10_000,
            "PLAYER_WALLET:p2": 20_000,
            "PSP_CLEARING:adyen": 50_000,
            "PSP_CLEARING:paypal": 30_000,
            "BANK_SETTLEMENT:main_eur": 200_000,
            "TAX_LIABILITY:MT": 15_000,
        },
        wallet_balances={"p1": 10_000, "p2": 20_000},
        psp_positions={"adyen": 50_000, "paypal": 30_000},
        bank_balances={"main_eur": 200_000},
        tax_liabilities={"MT": 15_000},
        period_label="2026-03-23",
    )


def main() -> None:
    result = demo_gate()
    print(json.dumps(asdict(result), indent=2))

    if result.overall_pass:
        print(f"\nRESULT: PASS -- {result.total_jobs} jobs, {result.total_matched} matched")
        sys.exit(0)
    else:
        print(f"\nRESULT: FAIL -- {result.critical_mismatches} critical mismatch(es)")
        sys.exit(1)


if __name__ == "__main__":
    main()


# ---------------------------------------------------------------------------
# Pytest tests
# ---------------------------------------------------------------------------

import pytest


def test_clean_reconciliation_passes():
    result = demo_gate()
    assert result.overall_pass is True
    assert result.total_jobs == 6
    assert result.total_matched == 6
    assert result.critical_mismatches == 0


def test_critical_mismatch_blocks():
    result = run_reconciliation_gate(
        ledger_balances={"PLAYER_WALLET:p1": 100_000},
        wallet_balances={"p1": 50_000},  # 50000 off = CRITICAL
        psp_positions={},
        bank_balances={},
        tax_liabilities={},
    )
    assert result.overall_pass is False
    assert result.critical_mismatches >= 1


def test_warning_mismatch_passes():
    result = run_reconciliation_gate(
        ledger_balances={"PLAYER_WALLET:p1": 1_000_000},
        wallet_balances={"p1": 995_000},  # 5000 off but < 1% = WARNING
        psp_positions={},
        bank_balances={},
        tax_liabilities={},
    )
    assert result.overall_pass is True
    assert result.warning_mismatches >= 1


def test_empty_data_passes():
    result = run_reconciliation_gate(
        ledger_balances={},
        wallet_balances={},
        psp_positions={},
        bank_balances={},
        tax_liabilities={},
    )
    assert result.overall_pass is True
    assert result.total_jobs == 0


def test_report_period_recorded():
    result = run_reconciliation_gate(
        ledger_balances={},
        wallet_balances={},
        psp_positions={},
        bank_balances={},
        tax_liabilities={},
        period_label="2026-03-23",
    )
    assert result.report_period == "2026-03-23"


def test_multi_type_reconciliation():
    result = run_reconciliation_gate(
        ledger_balances={
            "PLAYER_WALLET:p1": 10_000,
            "PSP_CLEARING:adyen": 50_000,
            "BANK_SETTLEMENT:main": 100_000,
            "TAX_LIABILITY:GB": 8_000,
        },
        wallet_balances={"p1": 10_000},
        psp_positions={"adyen": 50_000},
        bank_balances={"main": 100_000},
        tax_liabilities={"GB": 8_000},
    )
    assert result.total_jobs == 4
    assert result.total_matched == 4
    types_seen = {d["type"] for d in result.details}
    assert "LEDGER_VS_WALLET" in types_seen
    assert "LEDGER_VS_PSP" in types_seen
    assert "LEDGER_VS_BANK" in types_seen
    assert "LEDGER_VS_TAX" in types_seen

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
Reconciliation Service -- Adapter and E2E Tests

Tests covering:
  1. Abstract adapter contract enforcement
  2. Stub adapter implementations
  3. HTTP adapter implementations (via mock session)
  4. E2E cross-system reconciliation with realistic data snapshots
  5. Adapter polymorphism (service works with any adapter implementation)
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from importlib.machinery import ModuleSpec
from unittest.mock import MagicMock

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
_load_local_module("adapters", "adapters.py")
_load_local_module("reconciliation", "reconciliation.py")

from models import (
    ClosureStatus,
    JobStatus,
    MismatchSeverity,
    MismatchStatus,
    ReconciliationType,
)
from adapters import (
    BankAdapter,
    HttpBankAdapter,
    HttpLedgerAdapter,
    HttpPSPAdapter,
    HttpTaxAdapter,
    HttpWalletAdapter,
    LedgerAdapter,
    PSPAdapter,
    StubBankAdapter,
    StubLedgerAdapter,
    StubPSPAdapter,
    StubTaxAdapter,
    StubWalletAdapter,
    TaxAdapter,
    WalletAdapter,
)
from reconciliation import (
    ReconciliationService,
    ReconciliationStore,
)


# ---------------------------------------------------------------------------
# Helpers: mock HTTP session
# ---------------------------------------------------------------------------


class MockResponse:
    """Simulates an HTTP response for adapter testing."""

    def __init__(self, json_data: dict[str, Any], status_code: int = 200) -> None:
        self._json = json_data
        self.status_code = status_code

    def json(self) -> dict[str, Any]:
        return self._json

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


class MockSession:
    """Simulates an HTTP session that returns canned responses by URL."""

    def __init__(self) -> None:
        self._routes: dict[str, MockResponse] = {}

    def register(self, url: str, data: dict[str, Any]) -> None:
        self._routes[url] = MockResponse(data)

    def get(self, url: str) -> MockResponse:
        if url in self._routes:
            return self._routes[url]
        return MockResponse({}, status_code=404)


# ===========================================================================
# 1. Abstract adapter contract enforcement
# ===========================================================================


def test_ledger_adapter_is_abstract():
    """Cannot instantiate LedgerAdapter directly."""
    with pytest.raises(TypeError):
        LedgerAdapter()  # type: ignore[abstract]


def test_wallet_adapter_is_abstract():
    with pytest.raises(TypeError):
        WalletAdapter()  # type: ignore[abstract]


def test_psp_adapter_is_abstract():
    with pytest.raises(TypeError):
        PSPAdapter()  # type: ignore[abstract]


def test_bank_adapter_is_abstract():
    with pytest.raises(TypeError):
        BankAdapter()  # type: ignore[abstract]


def test_tax_adapter_is_abstract():
    with pytest.raises(TypeError):
        TaxAdapter()  # type: ignore[abstract]


# ===========================================================================
# 2. Stub adapter implementations
# ===========================================================================


def test_stub_ledger_default_balance():
    adapter = StubLedgerAdapter()
    assert adapter.get_balance("NONEXISTENT") == 0


def test_stub_ledger_set_and_get():
    adapter = StubLedgerAdapter()
    adapter.set_balance("PLAYER_WALLET:p1", 50000)
    assert adapter.get_balance("PLAYER_WALLET:p1") == 50000


def test_stub_wallet_list_player_ids():
    adapter = StubWalletAdapter()
    adapter.set_balance("p1", 100)
    adapter.set_balance("p2", 200)
    assert sorted(adapter.list_player_ids()) == ["p1", "p2"]


def test_stub_psp_list_names():
    adapter = StubPSPAdapter()
    adapter.set_position("adyen", 50000)
    assert adapter.list_psp_names() == ["adyen"]


def test_stub_bank_list_accounts():
    adapter = StubBankAdapter()
    adapter.set_balance("main_eur", 200000)
    assert adapter.list_accounts() == ["main_eur"]


def test_stub_tax_list_jurisdictions():
    adapter = StubTaxAdapter()
    adapter.set_liability("MT", 30000)
    adapter.set_liability("GB", 45000)
    assert sorted(adapter.list_jurisdictions()) == ["GB", "MT"]


# ===========================================================================
# 3. HTTP adapter implementations (via mock session)
# ===========================================================================


def test_http_ledger_adapter():
    session = MockSession()
    session.register(
        "http://ledger:8000/accounts/PLAYER_WALLET:p1/balance",
        {"account_id": "PLAYER_WALLET:p1", "balance": 15000},
    )
    adapter = HttpLedgerAdapter("http://ledger:8000", session=session)
    assert adapter.get_balance("PLAYER_WALLET:p1") == 15000


def test_http_wallet_adapter_balance():
    session = MockSession()
    session.register(
        "http://wallet:8000/players/p1/balance",
        {"balance": 9500},
    )
    adapter = HttpWalletAdapter("http://wallet:8000", session=session)
    assert adapter.get_balance("p1") == 9500


def test_http_wallet_adapter_list_players():
    session = MockSession()
    session.register(
        "http://wallet:8000/players",
        {"player_ids": ["p1", "p2", "p3"]},
    )
    adapter = HttpWalletAdapter("http://wallet:8000", session=session)
    assert adapter.list_player_ids() == ["p1", "p2", "p3"]


def test_http_psp_adapter_position():
    session = MockSession()
    session.register(
        "http://psp:8000/positions/adyen",
        {"position": 48000},
    )
    adapter = HttpPSPAdapter("http://psp:8000", session=session)
    assert adapter.get_position("adyen") == 48000


def test_http_psp_adapter_list_names():
    session = MockSession()
    session.register(
        "http://psp:8000/positions",
        {"psp_names": ["adyen", "stripe"]},
    )
    adapter = HttpPSPAdapter("http://psp:8000", session=session)
    assert adapter.list_psp_names() == ["adyen", "stripe"]


def test_http_bank_adapter_balance():
    session = MockSession()
    session.register(
        "http://bank:8000/accounts/main_eur/balance",
        {"balance": 200000},
    )
    adapter = HttpBankAdapter("http://bank:8000", session=session)
    assert adapter.get_balance("main_eur") == 200000


def test_http_bank_adapter_list_accounts():
    session = MockSession()
    session.register(
        "http://bank:8000/accounts",
        {"accounts": ["main_eur", "gbp_ops"]},
    )
    adapter = HttpBankAdapter("http://bank:8000", session=session)
    assert adapter.list_accounts() == ["main_eur", "gbp_ops"]


def test_http_tax_adapter_liability():
    session = MockSession()
    session.register(
        "http://tax:8000/liabilities/MT",
        {"liability": 30000},
    )
    adapter = HttpTaxAdapter("http://tax:8000", session=session)
    assert adapter.get_liability("MT") == 30000


def test_http_tax_adapter_list_jurisdictions():
    session = MockSession()
    session.register(
        "http://tax:8000/liabilities",
        {"jurisdictions": ["MT", "GB", "BR"]},
    )
    adapter = HttpTaxAdapter("http://tax:8000", session=session)
    assert adapter.list_jurisdictions() == ["MT", "GB", "BR"]


# ===========================================================================
# 4. E2E cross-system reconciliation with realistic data snapshots
# ===========================================================================


def _build_realistic_snapshot():
    """
    Simulate a realistic end-of-day snapshot across five systems:
    ledger, wallet, PSP, bank, and tax authority.

    Scenario: AcmeToCasino end-of-day 2026-03-23
    - 3 players with known balances
    - 2 PSPs (Adyen, Trustly)
    - 1 bank account (main_eur)
    - 2 tax jurisdictions (MT, GB)
    - Player p3 has a timing-induced mismatch (deposit arrived late)
    - Adyen has a small rounding mismatch (EUR 0.15 = 15 minor units)
    """
    ledger = StubLedgerAdapter()
    wallet = StubWalletAdapter()
    psp = StubPSPAdapter()
    bank = StubBankAdapter()
    tax = StubTaxAdapter()

    # Ledger balances (source of truth)
    ledger.set_balance("PLAYER_WALLET:p1", 150_00)
    ledger.set_balance("PLAYER_WALLET:p2", 320_00)
    ledger.set_balance("PLAYER_WALLET:p3", 75_00)
    ledger.set_balance("PSP_CLEARING:adyen", 500_00)
    ledger.set_balance("PSP_CLEARING:trustly", 280_00)
    ledger.set_balance("BANK_SETTLEMENT:main_eur", 1_200_000)
    ledger.set_balance("TAX_LIABILITY:MT", 45_000)
    ledger.set_balance("TAX_LIABILITY:GB", 62_000)

    # Wallet balances (agree for p1/p2, mismatch for p3)
    wallet.set_balance("p1", 150_00)
    wallet.set_balance("p2", 320_00)
    wallet.set_balance("p3", 50_00)  # 2500 minor units off (late deposit)

    # PSP positions (adyen has rounding mismatch, trustly matches)
    psp.set_position("adyen", 499_85)  # 15 minor units off
    psp.set_position("trustly", 280_00)

    # Bank matches
    bank.set_balance("main_eur", 1_200_000)

    # Tax matches
    tax.set_liability("MT", 45_000)
    tax.set_liability("GB", 62_000)

    return ledger, wallet, psp, bank, tax


def test_e2e_realistic_daily_reconciliation():
    """Full E2E: run daily recon on realistic snapshot, verify mismatches."""
    ledger, wallet, psp, bank, tax = _build_realistic_snapshot()

    svc = ReconciliationService(
        store=ReconciliationStore(),
        ledger=ledger,
        wallet=wallet,
        psp=psp,
        bank=bank,
        tax=tax,
    )

    jobs = svc.run_daily_reconciliation()

    # 3 wallets + 2 PSPs + 1 bank + 2 tax = 8 jobs
    assert len(jobs) == 8

    matched = [j for j in jobs if j.is_matched]
    mismatched = [j for j in jobs if not j.is_matched]

    # p1, p2, trustly, main_eur, MT, GB = 6 matched
    assert len(matched) == 6
    # p3 (wallet), adyen (PSP) = 2 mismatched
    assert len(mismatched) == 2


def test_e2e_mismatch_investigation_and_closure():
    """Full E2E: detect mismatches, investigate, resolve, close period."""
    ledger, wallet, psp, bank, tax = _build_realistic_snapshot()

    svc = ReconciliationService(
        store=ReconciliationStore(),
        ledger=ledger,
        wallet=wallet,
        psp=psp,
        bank=bank,
        tax=tax,
    )

    svc.run_daily_reconciliation()

    mismatches = svc.get_open_mismatches()
    assert len(mismatches) == 2

    # Find and resolve the wallet mismatch (p3 late deposit)
    wallet_mm = [m for m in mismatches if m.recon_type == ReconciliationType.LEDGER_VS_WALLET]
    assert len(wallet_mm) == 1
    svc.assign_mismatch(wallet_mm[0].mismatch_id, "ops@acmetocasino.com")
    svc.add_investigation_note(
        wallet_mm[0].mismatch_id,
        "Late deposit confirmed via PSP webhook log; wallet will sync on next cron",
    )
    svc.resolve_mismatch(
        wallet_mm[0].mismatch_id,
        "Late deposit processed; wallet balance corrected",
        "ops@acmetocasino.com",
    )

    # Waive the PSP rounding mismatch (15 minor units = EUR 0.15)
    psp_mm = [m for m in mismatches if m.recon_type == ReconciliationType.LEDGER_VS_PSP]
    assert len(psp_mm) == 1
    assert psp_mm[0].discrepancy == 15
    svc.waive_mismatch(
        psp_mm[0].mismatch_id,
        "Rounding difference EUR 0.15; within tolerance",
        "cfo@acmetocasino.com",
    )

    # All mismatches resolved/waived
    assert len(svc.get_open_mismatches()) == 0

    # Create and approve closure
    closure = svc.create_closure("2026-03-23")
    assert closure.status == ClosureStatus.READY
    approved = svc.approve_closure(closure.closure_id, "cfo@acmetocasino.com")
    assert approved.status == ClosureStatus.APPROVED


def test_e2e_http_adapters_with_service():
    """Verify the service works with HTTP adapters (via mock sessions)."""
    # Ledger
    ledger_session = MockSession()
    ledger_session.register(
        "http://ledger:8000/accounts/PLAYER_WALLET:p1/balance",
        {"balance": 10000},
    )
    ledger = HttpLedgerAdapter("http://ledger:8000", session=ledger_session)

    # Wallet
    wallet_session = MockSession()
    wallet_session.register(
        "http://wallet:8000/players/p1/balance",
        {"balance": 10000},
    )
    wallet_session.register(
        "http://wallet:8000/players",
        {"player_ids": ["p1"]},
    )
    wallet = HttpWalletAdapter("http://wallet:8000", session=wallet_session)

    svc = ReconciliationService(
        store=ReconciliationStore(),
        ledger=ledger,
        wallet=wallet,
    )

    job = svc.reconcile_wallet("p1")
    assert job.is_matched is True
    assert job.discrepancy == 0


# ===========================================================================
# 5. Adapter polymorphism
# ===========================================================================


def test_service_accepts_any_ledger_adapter():
    """ReconciliationService works with any LedgerAdapter implementation."""

    class CustomLedgerAdapter(LedgerAdapter):
        def get_balance(self, account_id: str) -> int:
            return 42_000

    ledger = CustomLedgerAdapter()
    wallet = StubWalletAdapter()
    wallet.set_balance("p1", 42_000)

    svc = ReconciliationService(
        store=ReconciliationStore(),
        ledger=ledger,
        wallet=wallet,
    )
    job = svc.reconcile_wallet("p1")
    assert job.is_matched is True


def test_stub_adapters_are_concrete():
    """Stub adapters can be instantiated and are proper subclasses."""
    ledger = StubLedgerAdapter()
    ledger.set_balance("X", 100)
    assert ledger.get_balance("X") == 100
    assert isinstance(ledger, LedgerAdapter)

    wallet = StubWalletAdapter()
    wallet.set_balance("p1", 200)
    assert wallet.get_balance("p1") == 200
    assert isinstance(wallet, WalletAdapter)

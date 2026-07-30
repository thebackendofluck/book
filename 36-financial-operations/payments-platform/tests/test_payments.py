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
Test suite for AcmetoCasino payments platform.

Coverage:
  - PaymentStatus enum properties
  - WithdrawalStatus enum properties
  - State machine: valid and invalid transitions
  - Withdrawal state machine: full lifecycle
  - FraudChecker: ALLOW / REVIEW / BLOCK scenarios
  - DepositLimitService: boundary checks
  - WithdrawalLimitService: KYC gate + auto-review threshold
  - PSPRouter: primary success, fallback on failure, no-PSP error
  - DepositService: full happy path, fraud block, limit rejection
  - WithdrawalService: full lifecycle, admin approve/reject
  - ReconciliationEngine: clean, amount mismatch, ghost transaction
  - PSPResponse model validation
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import os
from datetime import date
from importlib.machinery import ModuleSpec
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Local module loading
# ---------------------------------------------------------------------------
# chapter-36 ships multiple microservices with their own `models.py`
# (ledger-service, payments-platform, treasury-service). Pre-load this
# service's copies via importlib.util.spec_from_file_location before
# the plain `from models import ...` runs so sys.modules can't lose
# track of which file is which. See the matching preamble in
# ledger-service/tests/test_ledger.py for the full explanation.
SERVICE_DIR = Path(__file__).resolve().parent.parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))


def _load_local_module(module_name: str, file_name: str):
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(
        module_name, SERVICE_DIR / file_name,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(cast(ModuleSpec, spec))
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


# Evict any `psp*` package that a sibling test file may have installed
# first -- the inner payments-platform ships a different copy of the
# PSP package with slightly different adapter behaviour. Popping
# forces Python to re-resolve it through sys.path (where SERVICE_DIR
# is at index 0).
for _stale in [k for k in list(sys.modules) if k == "psp" or k.startswith("psp.")]:
    sys.modules.pop(_stale, None)

_load_local_module("models", "models.py")
_load_local_module("state_machine", "state_machine.py")
_load_local_module("fraud_check", "fraud_check.py")
_load_local_module("psp_router", "psp_router.py")
_load_local_module("deposit_service", "deposit_service.py")
_load_local_module("withdrawal_service", "withdrawal_service.py")
_load_local_module("reconciliation", "reconciliation.py")


@pytest.fixture(autouse=True, scope="module")
def _pin_local_modules():
    """Re-pin this service's modules so the lazy
    `from models import FailureInfo` inside state_machine.transition()
    and `from main import app` inside test bodies keep resolving to
    payments-platform's copies regardless of what other chapter-36
    test files overwrote during collection."""
    for _stale in [k for k in list(sys.modules) if k == "psp" or k.startswith("psp.")]:
        sys.modules.pop(_stale, None)
    sys.modules.pop("main", None)
    _load_local_module("models", "models.py")
    _load_local_module("state_machine", "state_machine.py")
    _load_local_module("fraud_check", "fraud_check.py")
    _load_local_module("psp_router", "psp_router.py")
    _load_local_module("deposit_service", "deposit_service.py")
    _load_local_module("withdrawal_service", "withdrawal_service.py")
    _load_local_module("reconciliation", "reconciliation.py")
    _load_local_module("main", "main.py")
    yield


from models import (
    Deposit,
    DepositRequest,
    FraudDecision,
    FraudScore,
    KycStatus,
    PaymentMethod,
    PaymentProviderInfo,
    PaymentStatus,
    PSPResponse,
    Withdrawal,
    WithdrawalStatus,
)
from state_machine import (
    InvalidTransitionError,
    InvalidWithdrawalTransitionError,
    PaymentStateMachine,
    WithdrawalStateMachine,
)
from fraud_check import FraudChecker, InMemoryFraudStore
from psp_router import PSPRegistry, PSPRouter, RoutingRule
from psp.base import PSPAdapter
from deposit_service import (
    DepositLimitService,
    DepositService,
    PaymentEventBus,
    PaymentStore,
)
from withdrawal_service import (
    KycService,
    WithdrawalLimitService,
    WithdrawalService,
    WithdrawalStore,
)
from reconciliation import (
    PlatformTransaction,
    PlatformTransactionStore,
    ReconciliationEngine,
    TransactionType,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_deposit(**kwargs) -> Deposit:
    defaults: dict[str, Any] = dict(
        payment_id="pay-001",
        brand_id=1,
        user_id=42,
        amount=5000,
        currency="GBP",
        user_ip="1.2.3.4",
        method=PaymentMethod.CARD,
        country_code="GB",
        status=PaymentStatus.STARTED,
        provider_info=PaymentProviderInfo(provider_name="adyen"),
    )
    defaults.update(kwargs)
    return Deposit(
        payment_id=cast(str, defaults["payment_id"]),
        brand_id=cast(int, defaults["brand_id"]),
        user_id=cast(int, defaults["user_id"]),
        amount=cast(int, defaults["amount"]),
        currency=cast(str, defaults["currency"]),
        user_ip=cast(str, defaults["user_ip"]),
        method=cast(PaymentMethod, defaults["method"]),
        country_code=cast(str, defaults["country_code"]),
        status=cast(PaymentStatus, defaults["status"]),
        provider_info=cast(PaymentProviderInfo, defaults["provider_info"]),
    )


def make_withdrawal(**kwargs) -> Withdrawal:
    defaults: dict[str, Any] = dict(
        withdrawal_id="wdraw-001",
        brand_id=1,
        user_id=42,
        amount=10000,
        currency="GBP",
        method=PaymentMethod.BANK_TRANSFER,
        status=WithdrawalStatus.STARTED,
    )
    defaults.update(kwargs)
    return Withdrawal(
        withdrawal_id=cast(str, defaults["withdrawal_id"]),
        brand_id=cast(int, defaults["brand_id"]),
        user_id=cast(int, defaults["user_id"]),
        amount=cast(int, defaults["amount"]),
        currency=cast(str, defaults["currency"]),
        method=cast(PaymentMethod, defaults["method"]),
        status=cast(WithdrawalStatus, defaults["status"]),
    )


class SuccessStubAdapter(PSPAdapter):
    name = "stub_success"
    supports_withdrawals = True

    async def deposit(self, payment: Deposit) -> PSPResponse:
        return PSPResponse(
            success=True,
            external_transaction_id="EXT-001",
            status=PaymentStatus.SUCCEEDED,
            raw_response={},
        )

    async def get_transaction_status(self, external_id: str) -> PSPResponse:
        return PSPResponse(
            success=True,
            external_transaction_id=external_id,
            status=PaymentStatus.SUCCEEDED,
            raw_response={},
        )

    async def withdraw(self, withdrawal: Withdrawal) -> PSPResponse:
        return PSPResponse(
            success=True,
            external_transaction_id="EXT-W-001",
            status=PaymentStatus.SUCCEEDED,
            raw_response={},
        )


class FailStubAdapter(PSPAdapter):
    name = "stub_fail"
    supports_withdrawals = False

    async def deposit(self, payment: Deposit) -> PSPResponse:
        return PSPResponse(
            success=False,
            status=PaymentStatus.FAILED,
            error_code="DECLINED",
            error_message="Card declined",
            raw_response={},
        )

    async def get_transaction_status(self, external_id: str) -> PSPResponse:
        return PSPResponse(
            success=False,
            external_transaction_id=external_id,
            status=PaymentStatus.FAILED,
            raw_response={},
        )


def make_router(primary: PSPAdapter, fallback: PSPAdapter | None = None) -> PSPRouter:
    registry = PSPRegistry()
    registry.register(primary)
    if fallback:
        registry.register(fallback)
    router = PSPRouter(registry)
    fallbacks = [fallback.name] if fallback else []
    router.add_rule(
        RoutingRule(PaymentMethod.CARD, "*", primary=primary.name, fallbacks=fallbacks)
    )
    router.add_rule(
        RoutingRule(PaymentMethod.BANK_TRANSFER, "*", primary=primary.name, fallbacks=fallbacks)
    )
    router.add_rule(
        RoutingRule(PaymentMethod.BOLETO, "BR", primary=primary.name, fallbacks=fallbacks)
    )
    router.add_rule(
        RoutingRule(PaymentMethod.SKRILL, "*", primary=primary.name, fallbacks=fallbacks)
    )
    return router


def make_deposit_service(router: PSPRouter | None = None) -> DepositService:
    r = router or make_router(SuccessStubAdapter())
    return DepositService(
        psp_router=r,
        fraud_checker=FraudChecker(InMemoryFraudStore()),
        limit_service=DepositLimitService(),
        store=PaymentStore(),
        event_bus=PaymentEventBus(),
    )


def make_withdrawal_service(router: PSPRouter | None = None) -> WithdrawalService:
    r = router or make_router(SuccessStubAdapter())
    return WithdrawalService(
        psp_router=r,
        kyc_service=KycService(),
        limit_service=WithdrawalLimitService(),
        store=WithdrawalStore(),
    )


# ---------------------------------------------------------------------------
# 1. PaymentStatus properties
# ---------------------------------------------------------------------------


def test_terminal_states():
    terminal = {
        PaymentStatus.SUCCEEDED, PaymentStatus.FAILED, PaymentStatus.ABANDONED,
        PaymentStatus.VOIDED, PaymentStatus.VOID_FAILED, PaymentStatus.REFUNDED,
    }
    non_terminal = {
        PaymentStatus.STARTED, PaymentStatus.PENDING, PaymentStatus.PROCESSING, PaymentStatus.VERIFY,
    }
    for s in terminal:
        assert s.is_terminal, f"{s} should be terminal"
    for s in non_terminal:
        assert not s.is_terminal, f"{s} should not be terminal"


def test_locking_states():
    assert PaymentStatus.PROCESSING.is_locking
    assert PaymentStatus.SUCCEEDED.is_locking
    assert not PaymentStatus.FAILED.is_locking
    assert not PaymentStatus.PENDING.is_locking


# ---------------------------------------------------------------------------
# 2. State machine — valid transitions
# ---------------------------------------------------------------------------


def test_payment_happy_path():
    sm = PaymentStateMachine()
    p = make_deposit()
    p = sm.pending(p)
    assert p.status == PaymentStatus.PENDING
    p = sm.processing(p)
    assert p.status == PaymentStatus.PROCESSING
    p = sm.succeed(p)
    assert p.status == PaymentStatus.SUCCEEDED


def test_payment_3ds_path():
    sm = PaymentStateMachine()
    p = make_deposit()
    p = sm.pending(p)
    p = sm.processing(p)
    p = sm.verify(p)
    assert p.status == PaymentStatus.VERIFY
    p = sm.processing(p)
    p = sm.succeed(p)
    assert p.status == PaymentStatus.SUCCEEDED


def test_payment_fail_transition():
    sm = PaymentStateMachine()
    p = make_deposit()
    p = sm.pending(p)
    p = sm.processing(p)
    p = sm.fail(p, "DECLINED", "Card declined by issuer")
    assert p.status == PaymentStatus.FAILED
    assert p.failure_info.failure_type == "DECLINED"


def test_payment_refund_from_succeeded():
    sm = PaymentStateMachine()
    p = make_deposit(status=PaymentStatus.SUCCEEDED)
    p = sm.refund(p)
    assert p.status == PaymentStatus.REFUNDED


def test_payment_void_flow():
    sm = PaymentStateMachine()
    p = make_deposit(status=PaymentStatus.PROCESSING)
    p = sm.void(p)
    assert p.status == PaymentStatus.VOIDING
    p = sm.voided(p)
    assert p.status == PaymentStatus.VOIDED


# ---------------------------------------------------------------------------
# 3. State machine — invalid transitions
# ---------------------------------------------------------------------------


def test_invalid_transition_raises():
    sm = PaymentStateMachine()
    p = make_deposit(status=PaymentStatus.SUCCEEDED)
    with pytest.raises(InvalidTransitionError):
        sm.processing(p)


def test_cannot_refund_from_pending():
    sm = PaymentStateMachine()
    p = make_deposit(status=PaymentStatus.PENDING)
    with pytest.raises(InvalidTransitionError):
        sm.refund(p)


def test_terminal_cannot_transition_to_processing():
    sm = PaymentStateMachine()
    for terminal_status in [PaymentStatus.FAILED, PaymentStatus.VOIDED, PaymentStatus.REFUNDED]:
        p = make_deposit(status=terminal_status)
        with pytest.raises(InvalidTransitionError):
            sm.processing(p)


# ---------------------------------------------------------------------------
# 4. Withdrawal state machine
# ---------------------------------------------------------------------------


def test_withdrawal_happy_path():
    sm = WithdrawalStateMachine()
    w = make_withdrawal()
    w = sm.submit(w)
    assert w.status == WithdrawalStatus.PENDING
    w = sm.accept(w)
    assert w.status == WithdrawalStatus.ACCEPTED
    w = sm.process(w)
    assert w.status == WithdrawalStatus.PROCESSING
    w = sm.reverse(w)
    assert w.status == WithdrawalStatus.REVERSED


def test_withdrawal_review_and_reject():
    sm = WithdrawalStateMachine()
    w = make_withdrawal()
    w = sm.submit(w)
    w = sm.flag_review(w)
    assert w.status == WithdrawalStatus.REVIEW
    w = sm.reject(w, "Failed AML check")
    assert w.status == WithdrawalStatus.REJECTED
    assert w.error_message == "Failed AML check"


def test_invalid_withdrawal_transition():
    sm = WithdrawalStateMachine()
    w = make_withdrawal(status=WithdrawalStatus.REJECTED)
    with pytest.raises(InvalidWithdrawalTransitionError):
        sm.accept(w)


# ---------------------------------------------------------------------------
# 5. Fraud checker
# ---------------------------------------------------------------------------


def test_fraud_allow():
    checker = FraudChecker(InMemoryFraudStore())
    deposit = make_deposit(user_ip="80.100.100.1")
    result = checker.evaluate(deposit)
    assert result.decision == FraudDecision.ALLOW


def test_fraud_block_blacklisted_user():
    store = InMemoryFraudStore()
    store.is_blacklisted_user = cast(Any, lambda uid: True)
    checker = FraudChecker(store)
    result = checker.evaluate(make_deposit())
    assert result.decision == FraudDecision.BLOCK
    assert "blacklisted_user" in result.signals


def test_fraud_block_blacklisted_ip():
    store = InMemoryFraudStore()
    store.is_blacklisted_ip = cast(Any, lambda ip: True)
    checker = FraudChecker(store)
    result = checker.evaluate(make_deposit())
    assert result.decision == FraudDecision.BLOCK


def test_fraud_review_datacenter_ip():
    store = InMemoryFraudStore()
    checker = FraudChecker(store)
    # InMemoryFraudStore treats 10.x.x.x as datacenter
    result = checker.evaluate(make_deposit(user_ip="10.0.0.1"))
    # datacenter IP alone raises score but below block threshold
    assert result.decision in {FraudDecision.REVIEW, FraudDecision.ALLOW}


# ---------------------------------------------------------------------------
# 6. DepositLimitService
# ---------------------------------------------------------------------------


def test_deposit_limit_zero():
    svc = DepositLimitService()
    err = svc.check(1, 1, PaymentMethod.CARD, 0, "GBP", "GB")
    assert err is not None


def test_deposit_limit_below_minimum():
    svc = DepositLimitService()
    err = svc.check(1, 1, PaymentMethod.CARD, 50, "GBP", "GB")
    assert err is not None


def test_deposit_limit_exceeds_max():
    svc = DepositLimitService()
    err = svc.check(1, 1, PaymentMethod.CARD, 999_999_999, "GBP", "GB")
    assert err is not None


def test_deposit_limit_valid():
    svc = DepositLimitService()
    err = svc.check(1, 1, PaymentMethod.CARD, 5000, "GBP", "GB")
    assert err is None


# ---------------------------------------------------------------------------
# 7. PSP Router
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_router_primary_success():
    adapter = SuccessStubAdapter()
    router = make_router(adapter)
    deposit = make_deposit()
    response, psp_name = await router.route_deposit(deposit)
    assert response.success
    assert psp_name == "stub_success"


@pytest.mark.asyncio
async def test_router_fallback_on_primary_failure():
    fail_adapter = FailStubAdapter()
    success_adapter = SuccessStubAdapter()
    success_adapter.name = "stub_fallback"
    router = make_router(fail_adapter, success_adapter)
    deposit = make_deposit()
    response, psp_name = await router.route_deposit(deposit)
    assert response.success
    assert psp_name == "stub_fallback"


@pytest.mark.asyncio
async def test_router_no_psp_raises():
    registry = PSPRegistry()
    router = PSPRouter(registry)
    deposit = make_deposit()
    with pytest.raises(RuntimeError, match="No PSP configured"):
        await router.route_deposit(deposit)


# ---------------------------------------------------------------------------
# 8. DepositService — full integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deposit_service_happy_path():
    svc = make_deposit_service()
    req = DepositRequest(
        brand_id=1,
        user_id=42,
        amount=5000,
        currency="GBP",
        user_ip="80.100.100.1",
        method=PaymentMethod.CARD,
        country_code="GB",
    )
    deposit = await svc.initiate(req)
    assert deposit.status == PaymentStatus.SUCCEEDED
    assert deposit.provider_info.provider_name == "stub_success"


@pytest.mark.asyncio
async def test_deposit_service_blocked_by_fraud():
    store = InMemoryFraudStore()
    store.is_blacklisted_user = cast(Any, lambda uid: True)
    svc = DepositService(
        psp_router=make_router(SuccessStubAdapter()),
        fraud_checker=FraudChecker(store),
        limit_service=DepositLimitService(),
        store=PaymentStore(),
        event_bus=PaymentEventBus(),
    )
    req = DepositRequest(
        brand_id=1, user_id=99, amount=5000, currency="GBP",
        user_ip="1.1.1.1", method=PaymentMethod.CARD, country_code="GB",
    )
    with pytest.raises(ValueError, match="fraud"):
        await svc.initiate(req)


@pytest.mark.asyncio
async def test_deposit_service_limit_rejection():
    svc = make_deposit_service()
    req = DepositRequest(
        brand_id=1, user_id=42, amount=0, currency="GBP",
        user_ip="1.1.1.1", method=PaymentMethod.CARD, country_code="GB",
    )
    with pytest.raises(ValueError, match="Deposit rejected"):
        await svc.initiate(req)


@pytest.mark.asyncio
async def test_deposit_service_callback_updates_status():
    svc = make_deposit_service()
    req = DepositRequest(
        brand_id=1, user_id=42, amount=5000, currency="GBP",
        user_ip="80.100.100.1", method=PaymentMethod.CARD, country_code="GB",
    )
    # Start in VERIFY state — override with a failing stub
    fail_adapter = FailStubAdapter()
    fail_adapter.name = "stub_fail_for_cb"

    class VerifyAdapter(SuccessStubAdapter):
        name = "verify_adapter"
        async def deposit(self, payment: Deposit) -> PSPResponse:
            return PSPResponse(
                success=True,
                external_transaction_id="EXT-VERIFY",
                status=PaymentStatus.VERIFY,
                raw_response={},
            )

    router = make_router(VerifyAdapter())
    svc2 = make_deposit_service(router)
    deposit = await svc2.initiate(req)
    assert deposit.status == PaymentStatus.VERIFY

    # VERIFY → PROCESSING (3DS auth complete, awaiting settlement)
    cb_processing = PSPResponse(
        success=True,
        external_transaction_id="EXT-VERIFY",
        status=PaymentStatus.PROCESSING,
        raw_response={},
    )
    deposit = await svc2.handle_psp_callback(deposit.payment_id, cb_processing)
    assert deposit.status == PaymentStatus.PROCESSING

    # PROCESSING → SUCCEEDED (settlement confirmed)
    cb_success = PSPResponse(
        success=True,
        external_transaction_id="EXT-VERIFY",
        status=PaymentStatus.SUCCEEDED,
        raw_response={},
    )
    deposit = await svc2.handle_psp_callback(deposit.payment_id, cb_success)
    assert deposit.status == PaymentStatus.SUCCEEDED


def test_fastapi_deposit_and_callback_flow():
    from main import app

    with TestClient(app) as client:
        create_resp = client.post(
            "/v1/deposits",
            json={
                "brand_id": 1,
                "user_id": 42,
                "amount": 2500,
                "currency": "BRL",
                "user_ip": "127.0.0.1",
                "method": "pix",
                "country_code": "BR",
                "language": "pt",
                "mobile": True,
                "params": {},
            },
        )
        assert create_resp.status_code == 201
        created = create_resp.json()
        assert created["method"] == "pix"
        assert created["status"] in {"SUCCEEDED", "PENDING"}

        payment_id = created["payment_id"]
        callback_resp = client.post(
            f"/v1/deposits/{payment_id}/callback",
            json={
                "external_transaction_id": created["external_id"],
                "status": "SUCCEEDED",
                "success": True,
                "raw_response": {"source": "psp-test"},
            },
        )
        assert callback_resp.status_code == 200
        updated = callback_resp.json()
        assert updated["payment_id"] == payment_id
        assert updated["status"] == "SUCCEEDED"


def test_fastapi_supports_boleto_deposit():
    from main import app

    with TestClient(app) as client:
        create_resp = client.post(
            "/v1/deposits",
            json={
                "brand_id": 7,
                "user_id": 77,
                "amount": 9900,
                "currency": "BRL",
                "user_ip": "10.0.0.7",
                "method": "boleto",
                "country_code": "BR",
                "language": "pt",
                "mobile": False,
                "params": {"document": "12345678901"},
            },
        )
        assert create_resp.status_code == 201
        payload = create_resp.json()
        assert payload["method"] == "boleto"
        assert payload["provider"] == "boleto"
        assert payload["status"] == "PROCESSING"
        assert payload["external_id"].startswith("BOL-")


def test_fastapi_supports_skrill_withdrawal():
    from main import app

    with TestClient(app) as client:
        create_resp = client.post(
            "/v1/withdrawals",
            json={
                "brand_id": 9,
                "user_id": 99,
                "amount": 4000,
                "currency": "EUR",
                "method": "skrill",
                "details": {"wallet_id": "skrill-user-99"},
            },
        )
        assert create_resp.status_code == 201
        payload = create_resp.json()
        assert payload["method"] == "skrill"
        assert payload["status"] == "REVERSED"
        assert payload["external_id"].startswith("WDR-")


# ---------------------------------------------------------------------------
# 9. WithdrawalService
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_withdrawal_auto_approved_and_processed():
    svc = make_withdrawal_service()
    w = await svc.request(
        user_id=42, brand_id=1, amount=1000, currency="GBP",
        method=PaymentMethod.BANK_TRANSFER, details={},
    )
    # Amount 1000 < AUTO_APPROVE_THRESHOLD (50000) → auto-approved and processed
    assert w.status in {WithdrawalStatus.REVERSED, WithdrawalStatus.FAILED}


@pytest.mark.asyncio
async def test_withdrawal_sent_to_review():
    svc = make_withdrawal_service()
    w = await svc.request(
        user_id=42, brand_id=1, amount=100_000, currency="GBP",
        method=PaymentMethod.BANK_TRANSFER, details={},
    )
    assert w.status == WithdrawalStatus.REVIEW


@pytest.mark.asyncio
async def test_withdrawal_admin_approve():
    svc = make_withdrawal_service()
    w = await svc.request(
        user_id=42, brand_id=1, amount=100_000, currency="GBP",
        method=PaymentMethod.BANK_TRANSFER, details={},
    )
    assert w.status == WithdrawalStatus.REVIEW
    approved = await svc.approve(w.withdrawal_id, admin_user_id=1)
    assert approved.status in {WithdrawalStatus.REVERSED, WithdrawalStatus.FAILED}


@pytest.mark.asyncio
async def test_withdrawal_admin_reject():
    svc = make_withdrawal_service()
    w = await svc.request(
        user_id=42, brand_id=1, amount=100_000, currency="GBP",
        method=PaymentMethod.BANK_TRANSFER, details={},
    )
    rejected = await svc.reject(w.withdrawal_id, admin_user_id=1, reason="AML flag")
    assert rejected.status == WithdrawalStatus.REJECTED


@pytest.mark.asyncio
async def test_withdrawal_kyc_blocked():
    kyc = KycService()
    kyc.get_status = cast(Any, lambda uid: KycStatus.PENDING)
    svc = WithdrawalService(
        psp_router=make_router(SuccessStubAdapter()),
        kyc_service=kyc,
        limit_service=WithdrawalLimitService(),
        store=WithdrawalStore(),
    )
    with pytest.raises(ValueError, match="KYC"):
        await svc.request(1, 1, 5000, "GBP", PaymentMethod.BANK_TRANSFER, {})


# ---------------------------------------------------------------------------
# 10. Reconciliation
# ---------------------------------------------------------------------------


def test_reconciliation_clean():
    store = PlatformTransactionStore()
    store.add(PlatformTransaction(
        transaction_id="T1",
        external_id="EXT-001",
        provider_name="adyen",
        transaction_type=TransactionType.DEPOSIT,
        amount=5000,
        currency="GBP",
        status="SUCCEEDED",
        settled_at=date(2025, 1, 1),
    ))
    engine = ReconciliationEngine(store)
    csv = "external_id,type,amount,currency,status\nEXT-001,deposit,5000,GBP,Settled\n"
    result = engine.reconcile(date(2025, 1, 1), "adyen", csv)
    assert result.discrepancy_amount == 0


def test_reconciliation_amount_mismatch():
    store = PlatformTransactionStore()
    store.add(PlatformTransaction(
        transaction_id="T2",
        external_id="EXT-002",
        provider_name="adyen",
        transaction_type=TransactionType.DEPOSIT,
        amount=5000,
        currency="GBP",
        status="SUCCEEDED",
        settled_at=date(2025, 1, 2),
    ))
    engine = ReconciliationEngine(store)
    csv = "external_id,type,amount,currency,status\nEXT-002,deposit,4999,GBP,Settled\n"
    result = engine.reconcile(date(2025, 1, 2), "adyen", csv)
    assert result.discrepancy_amount == 1
    assert "AMOUNT_MISMATCH" in result.notes


def test_reconciliation_ghost_transaction():
    """Transaction in PSP settlement but absent from platform — critical."""
    store = PlatformTransactionStore()
    engine = ReconciliationEngine(store)
    csv = "external_id,type,amount,currency,status\nGHOST-999,deposit,10000,GBP,Settled\n"
    result = engine.reconcile(date(2025, 1, 3), "adyen", csv)
    assert "MISSING_IN_PLATFORM" in result.notes
    assert result.discrepancy_amount == 10000

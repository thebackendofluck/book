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
AcmetoCasino Payments Platform — FastAPI entry point.

API surface:
  POST   /v1/deposits                   — initiate a deposit
  GET    /v1/deposits/{payment_id}      — fetch deposit status
  POST   /v1/deposits/{payment_id}/callback — PSP webhook / redirect callback
  POST   /v1/withdrawals                — request a withdrawal
  GET    /v1/withdrawals/{wid}          — fetch withdrawal status
  POST   /v1/withdrawals/{wid}/approve  — admin: approve withdrawal
  POST   /v1/withdrawals/{wid}/reject   — admin: reject withdrawal
  GET    /v1/withdrawals/review         — admin: list withdrawals awaiting review
  POST   /v1/reconciliation/run         — admin: trigger daily reconciliation
  GET    /health                        — liveness probe
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

import structlog

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from models import (
    Deposit,
    DepositRequest,
    PaymentMethod,
    PaymentStatus,
    PSPResponse,
    Withdrawal,
    WithdrawalStatus,
)
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
from fraud_check import FraudChecker, InMemoryFraudStore
from psp_router import PSPRegistry, PSPRouter, RoutingRule
from reconciliation import (
    PlatformTransactionStore,
    ReconciliationEngine,
    ReconciliationRecord,
)

structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO)
)
log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Dependency wiring (application-level singletons)
# ---------------------------------------------------------------------------


def _build_registry() -> PSPRegistry:
    """
    Register PSP adapters.

    In production each adapter is constructed from environment-variable config.
    For demo purposes all adapters use no-op stubs (no live PSP credentials needed).
    """
    from psp.base import PSPAdapter
    from models import PaymentStatus as PS

    class StubAdapter(PSPAdapter):
        def __init__(self, n: str) -> None:
            self.name = n

        async def deposit(self, payment: Deposit) -> PSPResponse:
            return PSPResponse(
                success=True,
                external_transaction_id=f"EXT-{payment.payment_id[:8]}",
                status=PS.SUCCEEDED,
                raw_response={"stub": True},
            )

        async def get_transaction_status(self, external_id: str) -> PSPResponse:
            return PSPResponse(
                success=True,
                external_transaction_id=external_id,
                status=PS.SUCCEEDED,
                raw_response={},
            )

    registry = PSPRegistry()
    for name in ["adyen", "paypal", "braintree", "trustly", "pix", "neteller"]:
        adapter = StubAdapter(name)
        adapter.supports_withdrawals = name in {"trustly", "neteller"}
        registry.register(adapter)
    return registry


def _build_router(registry: PSPRegistry) -> PSPRouter:
    router = PSPRouter(registry)
    router.add_rule(RoutingRule(PaymentMethod.CARD, "*", primary="adyen", fallbacks=["braintree"]))
    router.add_rule(RoutingRule(PaymentMethod.PAYPAL, "*", primary="paypal"))
    router.add_rule(RoutingRule(PaymentMethod.APPLE_PAY, "*", primary="adyen"))
    router.add_rule(RoutingRule(PaymentMethod.GOOGLE_PAY, "*", primary="adyen"))
    router.add_rule(RoutingRule(PaymentMethod.BANK_TRANSFER, "*", primary="trustly"))
    router.add_rule(RoutingRule(PaymentMethod.PIX, "BR", primary="pix"))
    router.add_rule(RoutingRule(PaymentMethod.NETELLER, "*", primary="neteller"))
    router.add_rule(RoutingRule(PaymentMethod.SKRILL, "*", primary="neteller"))
    router.add_rule(RoutingRule(PaymentMethod.TRUSTLY, "*", primary="trustly"))
    return router


# Global singletons
_registry = _build_registry()
_router = _build_router(_registry)
_payment_store = PaymentStore()
_withdrawal_store = WithdrawalStore()
_recon_store = PlatformTransactionStore()

deposit_service = DepositService(
    psp_router=_router,
    fraud_checker=FraudChecker(InMemoryFraudStore()),
    limit_service=DepositLimitService(),
    store=_payment_store,
    event_bus=PaymentEventBus(),
)
withdrawal_service = WithdrawalService(
    psp_router=_router,
    kyc_service=KycService(),
    limit_service=WithdrawalLimitService(),
    store=_withdrawal_store,
)
reconciliation_engine = ReconciliationEngine(_recon_store)


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("AcmetoCasino Payments Platform starting")
    yield
    log.info("AcmetoCasino Payments Platform shutting down")



# Browser origins allowed to call this service. A wildcard on a treasury or
# payments API is not a default anyone should copy, so it must be set.
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
    if o.strip()
]

app = FastAPI(
    title="AcmetoCasino Payments Platform",
    version="1.0.0",
    description="Multi-PSP payment processing: deposits, withdrawals, reconciliation.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class DepositInitiateRequest(BaseModel):
    brand_id: int
    user_id: int
    amount: int
    currency: str
    user_ip: str
    method: PaymentMethod
    country_code: str
    language: str = "en"
    mobile: bool = False
    bonus_group_id: int | None = None
    params: dict[str, Any] = {}


class WithdrawalRequest(BaseModel):
    brand_id: int
    user_id: int
    amount: int
    currency: str
    method: PaymentMethod
    details: dict[str, Any] = {}


class PSPCallbackRequest(BaseModel):
    external_transaction_id: str | None = None
    status: PaymentStatus
    success: bool
    error_code: str | None = None
    error_message: str | None = None
    raw_response: dict[str, Any] = {}


class AdminActionRequest(BaseModel):
    admin_user_id: int
    reason: str = ""


class ReconciliationRunRequest(BaseModel):
    report_date: str           # YYYY-MM-DD
    provider_settlements: dict[str, str]   # provider → CSV content


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/health", tags=["ops"])
async def health():
    return {"status": "ok", "service": "payments-platform"}


# ---------------------------------------------------------------------------
# Deposits
# ---------------------------------------------------------------------------


@app.post("/v1/deposits", response_model=dict, status_code=status.HTTP_201_CREATED, tags=["deposits"])
async def initiate_deposit(body: DepositInitiateRequest):
    req = DepositRequest(
        brand_id=body.brand_id,
        user_id=body.user_id,
        amount=body.amount,
        currency=body.currency,
        user_ip=body.user_ip,
        method=body.method,
        country_code=body.country_code,
        language=body.language,
        mobile=body.mobile,
        bonus_group_id=body.bonus_group_id,
        params=body.params,
    )
    try:
        deposit = await deposit_service.initiate(req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return _deposit_response(deposit)


@app.get("/v1/deposits/{payment_id}", response_model=dict, tags=["deposits"])
async def get_deposit(payment_id: str):
    try:
        deposit = await deposit_service.get_status(payment_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _deposit_response(deposit)


@app.post("/v1/deposits/{payment_id}/callback", response_model=dict, tags=["deposits"])
async def deposit_callback(payment_id: str, body: PSPCallbackRequest):
    psp_response = PSPResponse(
        success=body.success,
        external_transaction_id=body.external_transaction_id,
        status=body.status,
        raw_response=body.raw_response,
        error_code=body.error_code,
        error_message=body.error_message,
    )
    try:
        deposit = await deposit_service.handle_psp_callback(payment_id, psp_response)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _deposit_response(deposit)


# ---------------------------------------------------------------------------
# Withdrawals
# ---------------------------------------------------------------------------


@app.post("/v1/withdrawals", response_model=dict, status_code=status.HTTP_201_CREATED, tags=["withdrawals"])
async def request_withdrawal(body: WithdrawalRequest):
    try:
        withdrawal = await withdrawal_service.request(
            user_id=body.user_id,
            brand_id=body.brand_id,
            amount=body.amount,
            currency=body.currency,
            method=body.method,
            details=body.details,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _withdrawal_response(withdrawal)


@app.get("/v1/withdrawals/review", response_model=list, tags=["withdrawals", "admin"])
async def list_withdrawals_for_review():
    return [_withdrawal_response(w) for w in withdrawal_service.list_pending_review()]


@app.get("/v1/withdrawals/{withdrawal_id}", response_model=dict, tags=["withdrawals"])
async def get_withdrawal(withdrawal_id: str):
    w = withdrawal_service._store.get(withdrawal_id)
    if w is None:
        raise HTTPException(status_code=404, detail="Withdrawal not found")
    return _withdrawal_response(w)


@app.post("/v1/withdrawals/{withdrawal_id}/approve", response_model=dict, tags=["withdrawals", "admin"])
async def approve_withdrawal(withdrawal_id: str, body: AdminActionRequest):
    try:
        w = await withdrawal_service.approve(withdrawal_id, body.admin_user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _withdrawal_response(w)


@app.post("/v1/withdrawals/{withdrawal_id}/reject", response_model=dict, tags=["withdrawals", "admin"])
async def reject_withdrawal(withdrawal_id: str, body: AdminActionRequest):
    try:
        w = await withdrawal_service.reject(withdrawal_id, body.admin_user_id, body.reason)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _withdrawal_response(w)


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


@app.post("/v1/reconciliation/run", response_model=list, tags=["admin", "reconciliation"])
async def run_reconciliation(body: ReconciliationRunRequest):
    from datetime import date
    try:
        report_date = date.fromisoformat(body.report_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format — use YYYY-MM-DD")
    results = reconciliation_engine.reconcile_all_providers(
        report_date, body.provider_settlements
    )
    return [r.model_dump() for r in results]


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------


def _deposit_response(deposit: Deposit) -> dict:
    return {
        "payment_id": deposit.payment_id,
        "status": deposit.status.value,
        "amount": deposit.amount,
        "currency": deposit.currency,
        "method": deposit.method.value,
        "provider": deposit.provider_info.provider_name,
        "external_id": deposit.provider_info.external_transaction_id,
        "redirect_url": deposit.metadata.get("redirect_url"),
        "failure_info": {
            "type": deposit.failure_info.failure_type,
            "reason": deposit.failure_info.failure_reason,
        },
        "created_at": deposit.created_at.isoformat(),
        "updated_at": deposit.updated_at.isoformat(),
    }


def _withdrawal_response(withdrawal: Withdrawal) -> dict:
    return {
        "withdrawal_id": withdrawal.withdrawal_id,
        "status": withdrawal.status.value,
        "amount": withdrawal.amount,
        "currency": withdrawal.currency,
        "method": withdrawal.method.value,
        "external_id": withdrawal.external_id,
        "error_message": withdrawal.error_message,
        "created_at": withdrawal.created_at.isoformat(),
        "updated_at": withdrawal.updated_at.isoformat(),
    }

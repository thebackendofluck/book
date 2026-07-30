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
Deposit processing service.

Orchestrates the full deposit lifecycle:

  DepositRequest
      |
      v
  [1] Validate limits and payment method availability
      |
      v
  [2] Fraud pre-check — BLOCK/REVIEW/ALLOW
      |
      v
  [3] Create Payment record (STARTED)
      |
      v
  [4] Currency conversion (if needed)
      |
      v
  [5] Route to PSP → PROCESSING
      |
  +---+---+
  |       |
VERIFY  SUCCEEDED / FAILED
  |
  v
[3DS callback] → PROCESSING → SUCCEEDED / FAILED

Events are published on a message bus (stub included) for downstream
consumers: bonus service, reporting, KYC, etc.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from models import (
    Deposit,
    DepositRequest,
    FraudDecision,
    PaymentMethod,
    PaymentProviderInfo,
    PaymentStatus,
    PSPResponse,
)
from fraud_check import FraudChecker
from psp_router import PSPRouter
from state_machine import PaymentStateMachine, InvalidTransitionError

import structlog
log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Simple event bus stub
# ---------------------------------------------------------------------------


class PaymentEventBus:
    """Stub event bus.  Replace with Kafka / SNS / RabbitMQ publisher."""

    async def publish(self, event_type: str, payload: dict) -> None:
        log.info("EVENT %s: payment_id=%s", event_type, payload.get("payment_id"))


# ---------------------------------------------------------------------------
# Deposit limits stub
# ---------------------------------------------------------------------------


class DepositLimitService:
    """Validates deposit amount against per-method, per-brand, per-country limits."""

    def check(
        self,
        user_id: int,
        brand_id: int,
        method: PaymentMethod,
        amount: int,
        currency: str,
        country_code: str,
    ) -> Optional[str]:
        """Return an error message if the deposit is outside limits, else None."""
        if amount <= 0:
            return "Deposit amount must be greater than zero"
        if amount < 100:  # £1.00 minimum
            return f"Minimum deposit is 100 minor units, got {amount}"
        if amount > 50_000_00:  # £50,000 absolute cap
            return f"Deposit amount {amount} exceeds maximum allowed"
        return None


# ---------------------------------------------------------------------------
# In-memory payment store stub
# ---------------------------------------------------------------------------


class PaymentStore:
    """In-memory store for demo/testing.  Replace with PostgreSQL via SQLAlchemy."""

    def __init__(self) -> None:
        self._payments: dict[str, Deposit] = {}

    def save(self, payment: Deposit) -> Deposit:
        self._payments[payment.payment_id] = payment
        return payment

    def get(self, payment_id: str) -> Optional[Deposit]:
        return self._payments.get(payment_id)

    def list_by_user(self, user_id: int) -> list[Deposit]:
        return [p for p in self._payments.values() if p.user_id == user_id]


# ---------------------------------------------------------------------------
# Deposit service
# ---------------------------------------------------------------------------


class DepositService:
    """
    Main entry point for deposit processing.

    Injected dependencies allow full unit-test mocking of each layer.
    """

    def __init__(
        self,
        psp_router: PSPRouter,
        fraud_checker: FraudChecker,
        limit_service: DepositLimitService,
        store: PaymentStore,
        event_bus: PaymentEventBus,
    ) -> None:
        self._router = psp_router
        self._fraud = fraud_checker
        self._limits = limit_service
        self._store = store
        self._events = event_bus
        self._sm = PaymentStateMachine()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def initiate(self, request: DepositRequest) -> Deposit:
        """
        Validate, fraud-check, and submit a new deposit.

        Returns the Deposit in its post-PSP-call state.
        Raises ValueError on validation failure.
        Raises RuntimeError if no PSP is available.
        """
        # 1. Limit check
        limit_error = self._limits.check(
            user_id=request.user_id,
            brand_id=request.brand_id,
            method=request.method,
            amount=request.amount,
            currency=request.currency,
            country_code=request.country_code,
        )
        if limit_error:
            raise ValueError(f"Deposit rejected: {limit_error}")

        # 2. Create deposit record in STARTED state
        deposit = Deposit(
            payment_id=str(uuid.uuid4()),
            brand_id=request.brand_id,
            user_id=request.user_id,
            amount=request.amount,
            currency=request.currency,
            user_ip=request.user_ip,
            method=request.method,
            country_code=request.country_code,
            language=request.language,
            mobile=request.mobile,
            bonus_group_id=request.bonus_group_id,
            status=PaymentStatus.STARTED,
            provider_info=PaymentProviderInfo(provider_name="unknown"),
            metadata=request.params,
        )
        deposit = self._store.save(deposit)

        # 3. Fraud check
        fraud_result = self._fraud.evaluate(deposit)
        if fraud_result.decision == FraudDecision.BLOCK:
            deposit = self._sm.fail(deposit, "FRAUD_BLOCK", "Blocked by fraud engine")
            deposit = self._store.save(deposit)
            await self._events.publish(
                "deposit.fraud_blocked",
                {"payment_id": deposit.payment_id, "signals": fraud_result.signals},
            )
            raise ValueError(f"Deposit blocked by fraud engine: {fraud_result.signals}")

        if fraud_result.decision == FraudDecision.REVIEW:
            log.warning(
                "Deposit %s flagged for review (score=%.2f)", deposit.payment_id, fraud_result.score
            )
            # Allow through but tag for manual review
            deposit = deposit.model_copy(
                update={"metadata": {**deposit.metadata, "fraud_review": True, "fraud_score": fraud_result.score}}
            )

        # 4. Advance to PENDING
        deposit = self._sm.pending(deposit)
        deposit = self._store.save(deposit)

        # 5. Route to PSP
        deposit = self._sm.processing(deposit)
        deposit = self._store.save(deposit)

        try:
            psp_response, psp_name = await self._router.route_deposit(deposit)
        except RuntimeError as exc:
            deposit = self._sm.fail(deposit, "NO_PSP", str(exc))
            deposit = self._store.save(deposit)
            raise

        # 6. Apply PSP result
        deposit = self._apply_psp_response(deposit, psp_response, psp_name)
        deposit = self._store.save(deposit)

        await self._events.publish(
            "deposit.status_changed",
            {"payment_id": deposit.payment_id, "status": deposit.status.value},
        )
        return deposit

    async def handle_psp_callback(
        self, payment_id: str, psp_response: PSPResponse
    ) -> Deposit:
        """
        Process a PSP webhook / redirect callback.

        Handles both 3DS completion (VERIFY → PROCESSING → SUCCEEDED/FAILED)
        and direct status updates.
        """
        deposit = self._store.get(payment_id)
        if deposit is None:
            raise ValueError(f"Payment {payment_id} not found")

        if deposit.status.is_terminal:
            log.warning(
                "Received callback for terminal payment %s — ignoring", payment_id
            )
            return deposit

        deposit = self._apply_psp_response(deposit, psp_response, deposit.provider_info.provider_name)
        deposit = self._store.save(deposit)

        await self._events.publish(
            "deposit.status_changed",
            {"payment_id": deposit.payment_id, "status": deposit.status.value},
        )
        return deposit

    async def get_status(self, payment_id: str) -> Deposit:
        deposit = self._store.get(payment_id)
        if deposit is None:
            raise ValueError(f"Payment {payment_id} not found")
        return deposit

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _apply_psp_response(
        self, deposit: Deposit, response: PSPResponse, psp_name: str
    ) -> Deposit:
        provider_info = PaymentProviderInfo(
            provider_name=psp_name,
            external_transaction_id=response.external_transaction_id,
            extra=response.raw_response,
        )

        new_deposit = deposit.model_copy(
            update={"provider_info": provider_info, "updated_at": datetime.now(timezone.utc)}
        )

        try:
            if response.status == PaymentStatus.SUCCEEDED:
                new_deposit = self._sm.succeed(new_deposit)
            elif response.status == PaymentStatus.FAILED:
                new_deposit = self._sm.fail(
                    new_deposit,
                    response.error_code or "PSP_FAILURE",
                    response.error_message or "PSP declined",
                )
            elif response.status == PaymentStatus.VERIFY:
                new_deposit = self._sm.verify(new_deposit)
            elif response.status == PaymentStatus.ABANDONED:
                new_deposit = self._sm.abandon(new_deposit)
            elif response.status == PaymentStatus.PROCESSING:
                # VERIFY → PROCESSING (3DS completed) or PENDING → PROCESSING
                if self._sm.can_transition(new_deposit, PaymentStatus.PROCESSING):
                    new_deposit = self._sm.processing(new_deposit)
            elif response.status == PaymentStatus.PENDING:
                # Stay in current state; awaiting async confirmation
                pass
            else:
                log.info(
                    "PSP returned status %s for payment %s — no state change",
                    response.status,
                    deposit.payment_id,
                )
        except InvalidTransitionError as exc:
            log.error("State machine error: %s", exc)

        return new_deposit

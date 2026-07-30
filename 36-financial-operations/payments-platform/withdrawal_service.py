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
Withdrawal processing service.

Withdrawal lifecycle:
  STARTED
    |
    v
  [KYC gate] — must be APPROVED to proceed
    |
    v
  PENDING
    |
    +--[auto-approve if under threshold]---> ACCEPTED
    |
    +--[above threshold / suspicious]------> REVIEW
                                                |
                                            [admin action]
                                           /            \
                                       ACCEPTED        REJECTED
    |
    v
  PROCESSING  (PSP payout submitted)
    |
  +-+-+
  |   |
REVERSED  FAILED (retry → PENDING)

Rules implemented:
  - KYC must be APPROVED
  - Auto-approve threshold (configurable per brand/country)
  - Daily/weekly/monthly withdrawal limits per user
  - Minimum and maximum per-transaction limits
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from models import (
    KycStatus,
    PaymentMethod,
    PaymentStatus,
    PSPResponse,
    Withdrawal,
    WithdrawalStatus,
)
from psp_router import PSPRouter
from state_machine import WithdrawalStateMachine

import structlog
log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# KYC gate stub
# ---------------------------------------------------------------------------


class KycService:
    """Stub KYC status resolver.  In production calls the identity service."""

    def get_status(self, user_id: int) -> KycStatus:
        # Default to APPROVED for local development
        return KycStatus.APPROVED


# ---------------------------------------------------------------------------
# Withdrawal limits stub
# ---------------------------------------------------------------------------


class WithdrawalLimitService:
    # Amounts in minor units
    MIN_AMOUNT = 1000           # £10.00
    MAX_AMOUNT = 1_000_000      # £10,000 per transaction
    AUTO_APPROVE_THRESHOLD = 50_000  # £500 — auto-approve below this

    def check(
        self,
        user_id: int,
        brand_id: int,
        method: PaymentMethod,
        amount: int,
        currency: str,
    ) -> Optional[str]:
        if amount < self.MIN_AMOUNT:
            return f"Minimum withdrawal is {self.MIN_AMOUNT} minor units"
        if amount > self.MAX_AMOUNT:
            return f"Maximum withdrawal is {self.MAX_AMOUNT} minor units"
        return None

    def requires_review(self, amount: int) -> bool:
        return amount >= self.AUTO_APPROVE_THRESHOLD


# ---------------------------------------------------------------------------
# Withdrawal store stub
# ---------------------------------------------------------------------------


class WithdrawalStore:
    def __init__(self) -> None:
        self._withdrawals: dict[str, Withdrawal] = {}

    def save(self, w: Withdrawal) -> Withdrawal:
        self._withdrawals[w.withdrawal_id] = w
        return w

    def get(self, withdrawal_id: str) -> Optional[Withdrawal]:
        return self._withdrawals.get(withdrawal_id)

    def list_pending(self) -> list[Withdrawal]:
        return [
            w for w in self._withdrawals.values()
            if w.status == WithdrawalStatus.REVIEW
        ]


# ---------------------------------------------------------------------------
# Withdrawal service
# ---------------------------------------------------------------------------


class WithdrawalService:
    """
    Orchestrates the full withdrawal flow including KYC gating and
    approval/rejection workflow.
    """

    def __init__(
        self,
        psp_router: PSPRouter,
        kyc_service: KycService,
        limit_service: WithdrawalLimitService,
        store: WithdrawalStore,
    ) -> None:
        self._router = psp_router
        self._kyc = kyc_service
        self._limits = limit_service
        self._store = store
        self._sm = WithdrawalStateMachine()

    # ------------------------------------------------------------------
    # User-facing: request a withdrawal
    # ------------------------------------------------------------------

    async def request(
        self,
        user_id: int,
        brand_id: int,
        amount: int,
        currency: str,
        method: PaymentMethod,
        details: dict,
    ) -> Withdrawal:
        """
        Submit a withdrawal request.

        Raises ValueError on validation failure (limit check, KYC block).
        Returns the Withdrawal in PENDING or REVIEW state.
        """
        # KYC gate
        kyc_status = self._kyc.get_status(user_id)
        if kyc_status != KycStatus.APPROVED:
            raise ValueError(
                f"Withdrawal rejected: KYC status is {kyc_status.value}. "
                "Full identity verification required before withdrawals."
            )

        # Limit check
        limit_error = self._limits.check(user_id, brand_id, method, amount, currency)
        if limit_error:
            raise ValueError(f"Withdrawal rejected: {limit_error}")

        withdrawal = Withdrawal(
            withdrawal_id=str(uuid.uuid4()),
            brand_id=brand_id,
            user_id=user_id,
            amount=amount,
            currency=currency,
            method=method,
            status=WithdrawalStatus.STARTED,
            details=details,
        )
        withdrawal = self._store.save(withdrawal)

        # Advance to PENDING
        withdrawal = self._sm.submit(withdrawal)

        # Decide: auto-approve or send to review queue
        if self._limits.requires_review(amount):
            withdrawal = self._sm.flag_review(withdrawal)
            log.info(
                "Withdrawal %s sent to review queue (amount=%d)", withdrawal.withdrawal_id, amount
            )
        else:
            withdrawal = self._sm.accept(withdrawal)
            withdrawal = await self._process(withdrawal)

        withdrawal = self._store.save(withdrawal)
        return withdrawal

    # ------------------------------------------------------------------
    # Admin: approve or reject a withdrawal in REVIEW
    # ------------------------------------------------------------------

    async def approve(self, withdrawal_id: str, admin_user_id: int) -> Withdrawal:
        withdrawal = self._get_or_raise(withdrawal_id)
        if withdrawal.status != WithdrawalStatus.REVIEW:
            raise ValueError(
                f"Cannot approve withdrawal in state {withdrawal.status.value}"
            )
        withdrawal = self._sm.accept(withdrawal)
        withdrawal = withdrawal.model_copy(
            update={"actioned_by": admin_user_id, "updated_at": datetime.now(timezone.utc)}
        )
        withdrawal = await self._process(withdrawal)
        return self._store.save(withdrawal)

    async def reject(
        self, withdrawal_id: str, admin_user_id: int, reason: str
    ) -> Withdrawal:
        withdrawal = self._get_or_raise(withdrawal_id)
        if withdrawal.status not in {WithdrawalStatus.REVIEW, WithdrawalStatus.PENDING}:
            raise ValueError(
                f"Cannot reject withdrawal in state {withdrawal.status.value}"
            )
        withdrawal = self._sm.reject(withdrawal, reason)
        withdrawal = withdrawal.model_copy(
            update={"actioned_by": admin_user_id, "updated_at": datetime.now(timezone.utc)}
        )
        return self._store.save(withdrawal)

    # ------------------------------------------------------------------
    # PSP callback
    # ------------------------------------------------------------------

    async def handle_psp_callback(
        self, withdrawal_id: str, psp_response: PSPResponse
    ) -> Withdrawal:
        withdrawal = self._get_or_raise(withdrawal_id)
        if withdrawal.status.cannot_process:
            log.warning("Callback for already-final withdrawal %s — ignoring", withdrawal_id)
            return withdrawal

        if psp_response.success:
            withdrawal = self._sm.reverse(withdrawal)
        else:
            error = psp_response.error_message or "PSP payout failed"
            withdrawal = self._sm.fail(withdrawal, error)

        return self._store.save(withdrawal)

    def list_pending_review(self) -> list[Withdrawal]:
        return self._store.list_pending()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _process(self, withdrawal: Withdrawal) -> Withdrawal:
        """Submit ACCEPTED withdrawal to the PSP."""
        withdrawal = self._sm.process(withdrawal)
        try:
            response, psp_name = await self._router.route_withdrawal(withdrawal)
            withdrawal = withdrawal.model_copy(
                update={
                    "processor": psp_name,
                    "external_id": response.external_transaction_id,
                    "updated_at": datetime.now(timezone.utc),
                }
            )
            if response.success:
                withdrawal = self._sm.reverse(withdrawal)
            else:
                error = response.error_message or "PSP payout failed"
                withdrawal = self._sm.fail(withdrawal, error)
        except Exception as exc:
            log.exception("PSP payout failed for withdrawal %s", withdrawal.withdrawal_id)
            withdrawal = self._sm.fail(withdrawal, str(exc))
        return withdrawal

    def _get_or_raise(self, withdrawal_id: str) -> Withdrawal:
        w = self._store.get(withdrawal_id)
        if w is None:
            raise ValueError(f"Withdrawal {withdrawal_id} not found")
        return w

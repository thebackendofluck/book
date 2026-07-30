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
Payment state machine.

Models the full lifecycle of a payment:

    STARTED
      |
      +--[validation ok]---> PENDING
      |                         |
      |                   [PSP call initiated]
      |                         |
      |                      PROCESSING
      |                     /    |    \
      |            [3DS req]  [ok]  [error]
      |                |       |       |
      |             VERIFY  SUCCEEDED  FAILED
      |                |
      |          [auth complete]
      |                |
      |           PROCESSING --> SUCCEEDED / FAILED
      |
      +--[timeout]---> ABANDONED
      |
      +--[void request on PROCESSING] --> VOIDING --> VOIDED / VOID_FAILED

Refunds are modelled as a separate terminal state on SUCCEEDED payments.
"""

from __future__ import annotations

from typing import Optional, TypeVar

from models import Payment, PaymentStatus, WithdrawalStatus, Withdrawal

PaymentT = TypeVar("PaymentT", bound=Payment)
WithdrawalT = TypeVar("WithdrawalT", bound=Withdrawal)


# ---------------------------------------------------------------------------
# Allowed transitions
# ---------------------------------------------------------------------------

PAYMENT_TRANSITIONS: dict[PaymentStatus, set[PaymentStatus]] = {
    PaymentStatus.STARTED:     {PaymentStatus.PENDING, PaymentStatus.FAILED, PaymentStatus.ABANDONED},
    PaymentStatus.PENDING:     {PaymentStatus.PROCESSING, PaymentStatus.FAILED, PaymentStatus.ABANDONED},
    PaymentStatus.PROCESSING:  {PaymentStatus.SUCCEEDED, PaymentStatus.FAILED, PaymentStatus.VERIFY, PaymentStatus.VOIDING, PaymentStatus.ABANDONED},
    PaymentStatus.VERIFY:      {PaymentStatus.PROCESSING, PaymentStatus.FAILED, PaymentStatus.ABANDONED},
    PaymentStatus.VOIDING:     {PaymentStatus.VOIDED, PaymentStatus.VOID_FAILED},
    # Terminal states — no outbound transitions except SUCCEEDED -> REFUNDED
    PaymentStatus.SUCCEEDED:   {PaymentStatus.REFUNDED},
    PaymentStatus.FAILED:      set(),
    PaymentStatus.ABANDONED:   {PaymentStatus.SUCCEEDED},   # late PSP notification
    PaymentStatus.VOIDED:      set(),
    PaymentStatus.VOID_FAILED: set(),
    PaymentStatus.REFUNDED:    set(),
}

WITHDRAWAL_TRANSITIONS: dict[WithdrawalStatus, set[WithdrawalStatus]] = {
    WithdrawalStatus.STARTED:      {WithdrawalStatus.PENDING, WithdrawalStatus.INVALIDATED},
    WithdrawalStatus.PENDING:      {WithdrawalStatus.REVIEW, WithdrawalStatus.ACCEPTED, WithdrawalStatus.REJECTED},
    WithdrawalStatus.REVIEW:       {WithdrawalStatus.ACCEPTED, WithdrawalStatus.REJECTED, WithdrawalStatus.TIMED_OUT},
    WithdrawalStatus.ACCEPTED:     {WithdrawalStatus.PROCESSING},
    WithdrawalStatus.PROCESSING:   {WithdrawalStatus.REVERSED, WithdrawalStatus.FAILED},
    WithdrawalStatus.FAILED:       {WithdrawalStatus.PENDING},   # retry path
    WithdrawalStatus.REVERSED:     set(),
    WithdrawalStatus.REJECTED:     set(),
    WithdrawalStatus.TIMED_OUT:    set(),
    WithdrawalStatus.INVALIDATED:  set(),
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class InvalidTransitionError(Exception):
    def __init__(self, current: PaymentStatus, target: PaymentStatus) -> None:
        super().__init__(
            f"Cannot transition payment from {current.value} to {target.value}"
        )


class InvalidWithdrawalTransitionError(Exception):
    def __init__(self, current: WithdrawalStatus, target: WithdrawalStatus) -> None:
        super().__init__(
            f"Cannot transition withdrawal from {current.value} to {target.value}"
        )


# ---------------------------------------------------------------------------
# State machine helpers
# ---------------------------------------------------------------------------


class PaymentStateMachine:
    """Validates and applies state transitions for Payment objects."""

    def transition(
        self,
        payment: PaymentT,
        new_status: PaymentStatus,
        failure_type: Optional[str] = None,
        failure_reason: Optional[str] = None,
    ) -> PaymentT:
        allowed = PAYMENT_TRANSITIONS.get(payment.status, set())
        if new_status not in allowed:
            raise InvalidTransitionError(payment.status, new_status)

        from datetime import datetime, timezone
        from models import FailureInfo

        updated = payment.model_copy(
            update={
                "status": new_status,
                "updated_at": datetime.now(timezone.utc),
                "failure_info": FailureInfo(
                    failure_type=failure_type,
                    failure_reason=failure_reason,
                )
                if failure_type
                else payment.failure_info,
            }
        )
        return updated

    def can_transition(self, payment: Payment, new_status: PaymentStatus) -> bool:
        return new_status in PAYMENT_TRANSITIONS.get(payment.status, set())

    def pending(self, payment: PaymentT) -> PaymentT:
        return self.transition(payment, PaymentStatus.PENDING)

    def processing(self, payment: PaymentT) -> PaymentT:
        return self.transition(payment, PaymentStatus.PROCESSING)

    def verify(self, payment: PaymentT) -> PaymentT:
        return self.transition(payment, PaymentStatus.VERIFY)

    def succeed(self, payment: PaymentT) -> PaymentT:
        return self.transition(payment, PaymentStatus.SUCCEEDED)

    def fail(self, payment: PaymentT, failure_type: str, reason: str) -> PaymentT:
        return self.transition(payment, PaymentStatus.FAILED, failure_type, reason)

    def abandon(self, payment: PaymentT) -> PaymentT:
        return self.transition(payment, PaymentStatus.ABANDONED)

    def void(self, payment: PaymentT) -> PaymentT:
        return self.transition(payment, PaymentStatus.VOIDING)

    def voided(self, payment: PaymentT) -> PaymentT:
        return self.transition(payment, PaymentStatus.VOIDED)

    def refund(self, payment: PaymentT) -> PaymentT:
        return self.transition(payment, PaymentStatus.REFUNDED)


class WithdrawalStateMachine:
    """Validates and applies state transitions for Withdrawal objects."""

    def transition(
        self,
        withdrawal: WithdrawalT,
        new_status: WithdrawalStatus,
        error_message: Optional[str] = None,
    ) -> WithdrawalT:
        allowed = WITHDRAWAL_TRANSITIONS.get(withdrawal.status, set())
        if new_status not in allowed:
            raise InvalidWithdrawalTransitionError(withdrawal.status, new_status)

        from datetime import datetime, timezone

        updated = withdrawal.model_copy(
            update={
                "status": new_status,
                "updated_at": datetime.now(timezone.utc),
                **({"error_message": error_message} if error_message else {}),
            }
        )
        return updated

    def can_transition(self, withdrawal: Withdrawal, new_status: WithdrawalStatus) -> bool:
        return new_status in WITHDRAWAL_TRANSITIONS.get(withdrawal.status, set())

    def submit(self, withdrawal: WithdrawalT) -> WithdrawalT:
        return self.transition(withdrawal, WithdrawalStatus.PENDING)

    def flag_review(self, withdrawal: WithdrawalT) -> WithdrawalT:
        return self.transition(withdrawal, WithdrawalStatus.REVIEW)

    def accept(self, withdrawal: WithdrawalT) -> WithdrawalT:
        return self.transition(withdrawal, WithdrawalStatus.ACCEPTED)

    def reject(self, withdrawal: WithdrawalT, reason: str) -> WithdrawalT:
        return self.transition(withdrawal, WithdrawalStatus.REJECTED, reason)

    def process(self, withdrawal: WithdrawalT) -> WithdrawalT:
        return self.transition(withdrawal, WithdrawalStatus.PROCESSING)

    def reverse(self, withdrawal: WithdrawalT) -> WithdrawalT:
        return self.transition(withdrawal, WithdrawalStatus.REVERSED)

    def fail(self, withdrawal: WithdrawalT, reason: str) -> WithdrawalT:
        return self.transition(withdrawal, WithdrawalStatus.FAILED, reason)

    def invalidate(self, withdrawal: WithdrawalT) -> WithdrawalT:
        return self.transition(withdrawal, WithdrawalStatus.INVALIDATED)

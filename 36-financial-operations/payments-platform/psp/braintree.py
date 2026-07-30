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
Braintree PSP adapter (Braintree Python SDK wrapper).

Covers:
  - Card transactions via client token + nonce flow
  - PayPal via Braintree vault
  - Refunds and voids
  - Webhook signature verification

Docs: https://developer.paypal.com/braintree/docs
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from models import Deposit, PaymentStatus, PSPResponse, Withdrawal
from psp.base import PSPAdapter

logger = logging.getLogger(__name__)

# braintree SDK is optional at import time; will fail at runtime if missing
try:
    import braintree  # type: ignore
    _HAS_SDK = True
except ImportError:
    _HAS_SDK = False


class BraintreeSettings:
    def __init__(
        self,
        merchant_id: str,
        public_key: str,
        private_key: str,
        sandbox: bool = True,
    ) -> None:
        self.merchant_id = merchant_id
        self.public_key = public_key
        self.private_key = private_key
        self.sandbox = sandbox


class BraintreeAdapter(PSPAdapter):
    """Braintree adapter using the official Python SDK."""

    name = "braintree"
    supports_refunds = True
    supports_withdrawals = False

    def __init__(self, settings: BraintreeSettings) -> None:
        if not _HAS_SDK:
            raise RuntimeError("braintree package is not installed")
        self.settings = settings
        env = braintree.Environment.Sandbox if settings.sandbox else braintree.Environment.Production
        self._gateway = braintree.BraintreeGateway(
            braintree.Configuration(
                environment=env,
                merchant_id=settings.merchant_id,
                public_key=settings.public_key,
                private_key=settings.private_key,
            )
        )

    # ------------------------------------------------------------------
    # PSPAdapter interface
    # ------------------------------------------------------------------

    async def deposit(self, payment: Deposit) -> PSPResponse:
        """
        Submit a sale transaction.  The payment_method_nonce must be present
        in payment.metadata["payment_method_nonce"].
        """
        nonce = payment.metadata.get("payment_method_nonce")
        if not nonce:
            return self._failure_response("MISSING_NONCE", "payment_method_nonce not provided")

        amount_decimal = f"{payment.psp_amount / 100:.2f}"
        try:
            result = self._gateway.transaction.sale(
                {
                    "amount": amount_decimal,
                    "payment_method_nonce": nonce,
                    "order_id": payment.payment_id,
                    "options": {"submit_for_settlement": True},
                }
            )
            return self._parse_result(result)
        except Exception as exc:
            logger.exception("Braintree deposit failed")
            return self._failure_response("BT_ERROR", str(exc))

    async def get_transaction_status(self, external_id: str) -> PSPResponse:
        try:
            txn = self._gateway.transaction.find(external_id)
            status = _bt_status_to_payment_status(txn.status)
            return PSPResponse(
                success=status == PaymentStatus.SUCCEEDED,
                external_transaction_id=external_id,
                status=status,
                raw_response={"status": txn.status, "id": txn.id},
            )
        except Exception as exc:
            return self._failure_response("STATUS_ERROR", str(exc))

    async def refund(self, external_id: str, amount: int, currency: str) -> PSPResponse:
        amount_decimal = f"{amount / 100:.2f}"
        try:
            result = self._gateway.transaction.refund(external_id, amount_decimal)
            if result.is_success:
                return PSPResponse(
                    success=True,
                    external_transaction_id=result.transaction.id,
                    status=PaymentStatus.REFUNDED,
                    raw_response={},
                )
            return self._failure_response(
                "REFUND_FAILED",
                "; ".join(e.message for e in result.errors.deep_errors),
            )
        except Exception as exc:
            return self._failure_response("REFUND_ERROR", str(exc))

    async def void(self, external_id: str) -> PSPResponse:
        try:
            result = self._gateway.transaction.void(external_id)
            if result.is_success:
                return PSPResponse(
                    success=True,
                    external_transaction_id=result.transaction.id,
                    status=PaymentStatus.VOIDED,
                    raw_response={},
                )
            return self._failure_response("VOID_FAILED", "Void not successful")
        except Exception as exc:
            return self._failure_response("VOID_ERROR", str(exc))

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _parse_result(self, result: Any) -> PSPResponse:
        if result.is_success:
            return PSPResponse(
                success=True,
                external_transaction_id=result.transaction.id,
                status=PaymentStatus.SUCCEEDED,
                raw_response={"id": result.transaction.id},
            )
        errors = "; ".join(e.message for e in result.errors.deep_errors)
        return self._failure_response("BT_DECLINED", errors)


def _bt_status_to_payment_status(bt_status: str) -> PaymentStatus:
    mapping = {
        "authorized": PaymentStatus.PROCESSING,
        "submitted_for_settlement": PaymentStatus.PROCESSING,
        "settling": PaymentStatus.PROCESSING,
        "settled": PaymentStatus.SUCCEEDED,
        "voided": PaymentStatus.VOIDED,
        "failed": PaymentStatus.FAILED,
        "processor_declined": PaymentStatus.FAILED,
        "gateway_rejected": PaymentStatus.FAILED,
    }
    return mapping.get(bt_status, PaymentStatus.FAILED)

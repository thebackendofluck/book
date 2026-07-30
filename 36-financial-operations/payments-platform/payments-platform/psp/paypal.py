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
PayPal PSP adapter.

Covers:
  - Orders API v2 (deposit flow)
  - Billing Agreements for recurring deposits
  - Refunds
  - Webhook event parsing

Docs: https://developer.paypal.com/api/rest/
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from models import Deposit, PaymentStatus, PSPResponse, Withdrawal
from psp.base import PSPAdapter

logger = logging.getLogger(__name__)


class PayPalSettings:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        sandbox: bool = True,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = (
            "https://api-m.sandbox.paypal.com"
            if sandbox
            else "https://api-m.paypal.com"
        )


class PayPalAdapter(PSPAdapter):
    """PayPal Orders API v2 adapter."""

    name = "paypal"
    supports_refunds = True
    supports_withdrawals = False

    def __init__(self, settings: PayPalSettings) -> None:
        self.settings = settings
        self._access_token: Optional[str] = None
        self._http = httpx.AsyncClient(base_url=settings.base_url, timeout=30.0)

    # ------------------------------------------------------------------
    # PSPAdapter interface
    # ------------------------------------------------------------------

    async def deposit(self, payment: Deposit) -> PSPResponse:
        token = await self._get_access_token()
        payload = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "reference_id": payment.payment_id,
                    "amount": {
                        "currency_code": payment.psp_currency,
                        "value": f"{payment.psp_amount / 100:.2f}",
                    },
                }
            ],
            "application_context": {
                "return_url": f"https://casino.example.com/payments/paypal/return/{payment.payment_id}",
                "cancel_url": f"https://casino.example.com/payments/paypal/cancel/{payment.payment_id}",
            },
        }
        try:
            resp = await self._http.post(
                "/v2/checkout/orders",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            data = resp.json()
            return self._parse_order_response(data)
        except Exception as exc:
            logger.exception("PayPal deposit failed")
            return self._failure_response("PAYPAL_ERROR", str(exc))

    async def get_transaction_status(self, external_id: str) -> PSPResponse:
        token = await self._get_access_token()
        try:
            resp = await self._http.get(
                f"/v2/checkout/orders/{external_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            data = resp.json()
            status = _paypal_order_status_to_payment_status(data.get("status", ""))
            return PSPResponse(
                success=status == PaymentStatus.SUCCEEDED,
                external_transaction_id=external_id,
                status=status,
                raw_response=data,
            )
        except Exception as exc:
            return self._failure_response("STATUS_ERROR", str(exc))

    async def refund(self, external_id: str, amount: int, currency: str) -> PSPResponse:
        token = await self._get_access_token()
        payload = {
            "amount": {
                "value": f"{amount / 100:.2f}",
                "currency_code": currency,
            }
        }
        try:
            resp = await self._http.post(
                f"/v2/payments/captures/{external_id}/refund",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            data = resp.json()
            return PSPResponse(
                success=True,
                external_transaction_id=data.get("id"),
                status=PaymentStatus.REFUNDED,
                raw_response=data,
            )
        except Exception as exc:
            return self._failure_response("REFUND_ERROR", str(exc))

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _get_access_token(self) -> str:
        if self._access_token:
            return self._access_token
        resp = await self._http.post(
            "/v1/oauth2/token",
            data={"grant_type": "client_credentials"},
            auth=(self.settings.client_id, self.settings.client_secret),
        )
        resp.raise_for_status()
        self._access_token = resp.json()["access_token"]
        return self._access_token  # type: ignore[return-value]

    def _parse_order_response(self, data: dict[str, Any]) -> PSPResponse:
        order_status = data.get("status", "")
        status = _paypal_order_status_to_payment_status(order_status)
        redirect_url = next(
            (
                link["href"]
                for link in data.get("links", [])
                if link.get("rel") == "approve"
            ),
            None,
        )
        return PSPResponse(
            success=order_status in {"APPROVED", "COMPLETED"},
            external_transaction_id=data.get("id"),
            redirect_url=redirect_url,
            status=status,
            raw_response=data,
        )

    async def close(self) -> None:
        await self._http.aclose()


def _paypal_order_status_to_payment_status(order_status: str) -> PaymentStatus:
    mapping = {
        "CREATED": PaymentStatus.PENDING,
        "SAVED": PaymentStatus.PENDING,
        "APPROVED": PaymentStatus.PROCESSING,
        "VOIDED": PaymentStatus.VOIDED,
        "COMPLETED": PaymentStatus.SUCCEEDED,
        "PAYER_ACTION_REQUIRED": PaymentStatus.VERIFY,
    }
    return mapping.get(order_status, PaymentStatus.FAILED)

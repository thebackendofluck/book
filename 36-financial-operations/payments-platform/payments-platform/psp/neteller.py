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
Neteller (Paysafe) PSP adapter.

Neteller is an e-wallet heavily used in iGaming markets.
Uses the Paysafe REST API.

Docs: https://developer.paysafe.com/en/sdks/server-side/
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from models import Deposit, PaymentStatus, PSPResponse, Withdrawal
from psp.base import PSPAdapter

logger = logging.getLogger(__name__)


class NetellerSettings:
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        merchant_account_number: str,
        sandbox: bool = True,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.merchant_account_number = merchant_account_number
        self.base_url = (
            "https://api.test.paysafe.com"
            if sandbox
            else "https://api.paysafe.com"
        )


class NetellerAdapter(PSPAdapter):
    """Neteller e-wallet adapter for deposits and payouts."""

    name = "neteller"
    supports_refunds = True
    supports_withdrawals = True

    def __init__(self, settings: NetellerSettings) -> None:
        self.settings = settings
        self._http = httpx.AsyncClient(
            base_url=settings.base_url,
            auth=(settings.api_key, settings.api_secret),
            timeout=30.0,
        )

    # ------------------------------------------------------------------
    # PSPAdapter interface
    # ------------------------------------------------------------------

    async def deposit(self, payment: Deposit) -> PSPResponse:
        """Neteller redirect deposit — generates an order and returns a redirect URL."""
        payload = {
            "merchantRefNum": payment.payment_id,
            "amount": payment.psp_amount,
            "currencyCode": payment.psp_currency,
            "merchantAccount": self.settings.merchant_account_number,
            "returnLinks": [
                {
                    "rel": "on_success",
                    "href": f"https://casino.example.com/payments/neteller/success/{payment.payment_id}",
                },
                {
                    "rel": "on_failure",
                    "href": f"https://casino.example.com/payments/neteller/fail/{payment.payment_id}",
                },
            ],
        }
        try:
            resp = await self._http.post(
                "/netellerws/v1/orders", json=payload
            )
            resp.raise_for_status()
            data = resp.json()
            redirect_url = next(
                (l["href"] for l in data.get("links", []) if l.get("rel") == "redirect"),
                None,
            )
            return PSPResponse(
                success=True,
                external_transaction_id=data.get("id"),
                redirect_url=redirect_url,
                status=PaymentStatus.PENDING,
                raw_response=data,
            )
        except Exception as exc:
            logger.exception("Neteller deposit failed")
            return self._failure_response("NETELLER_ERROR", str(exc))

    async def get_transaction_status(self, external_id: str) -> PSPResponse:
        try:
            resp = await self._http.get(f"/netellerws/v1/orders/{external_id}")
            resp.raise_for_status()
            data = resp.json()
            status = _neteller_status_to_payment_status(data.get("status", ""))
            return PSPResponse(
                success=status == PaymentStatus.SUCCEEDED,
                external_transaction_id=external_id,
                status=status,
                raw_response=data,
            )
        except Exception as exc:
            return self._failure_response("STATUS_ERROR", str(exc))

    async def withdraw(self, withdrawal: Withdrawal) -> PSPResponse:
        payload = {
            "merchantRefNum": withdrawal.withdrawal_id,
            "amount": withdrawal.amount,
            "currencyCode": withdrawal.currency,
            "paymentToken": withdrawal.details.get("neteller_account"),
        }
        try:
            resp = await self._http.post("/netellerws/v1/payouts", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return PSPResponse(
                success=True,
                external_transaction_id=data.get("id"),
                status=PaymentStatus.PROCESSING,
                raw_response=data,
            )
        except Exception as exc:
            return self._failure_response("NETELLER_PAYOUT_ERROR", str(exc))

    async def close(self) -> None:
        await self._http.aclose()


def _neteller_status_to_payment_status(status: str) -> PaymentStatus:
    mapping = {
        "PENDING": PaymentStatus.PENDING,
        "FAILED": PaymentStatus.FAILED,
        "COMPLETED": PaymentStatus.SUCCEEDED,
        "CANCELLED": PaymentStatus.VOIDED,
    }
    return mapping.get(status.upper(), PaymentStatus.FAILED)

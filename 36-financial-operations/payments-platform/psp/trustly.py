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
Trustly PSP adapter (Open Banking / bank transfer).

Trustly is used primarily for European bank-to-bank deposits and withdrawals.
The integration uses Trustly's JSON-RPC 1.1 API with RSA request signing.

Docs: https://developers.trustly.com/
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

import httpx

from models import Deposit, PaymentStatus, PSPResponse, Withdrawal
from psp.base import PSPAdapter

logger = logging.getLogger(__name__)


class TrustlySettings:
    def __init__(
        self,
        username: str,
        password: str,
        private_key_pem: str,
        sandbox: bool = True,
    ) -> None:
        self.username = username
        self.password = password
        self.private_key_pem = private_key_pem
        self.base_url = (
            "https://test.trustly.com/api/1"
            if sandbox
            else "https://trustly.com/api/1"
        )


class TrustlyAdapter(PSPAdapter):
    """
    Trustly JSON-RPC adapter.

    Deposits initiate a bank redirect; withdrawals are submitted directly
    to the consumer's registered bank account.
    """

    name = "trustly"
    supports_refunds = False
    supports_withdrawals = True

    def __init__(self, settings: TrustlySettings) -> None:
        self.settings = settings
        self._http = httpx.AsyncClient(timeout=30.0)

    # ------------------------------------------------------------------
    # PSPAdapter interface
    # ------------------------------------------------------------------

    async def deposit(self, payment: Deposit) -> PSPResponse:
        method_id = str(uuid.uuid4())
        payload = self._build_rpc_request(
            method="Deposit",
            params={
                "Attributes": {
                    "Currency": payment.psp_currency,
                    "Amount": f"{payment.psp_amount / 100:.2f}",
                    "Country": payment.country_code,
                    "Locale": payment.language,
                    "IP": payment.user_ip,
                },
                "EndUserID": str(payment.user_id),
                "MessageID": payment.payment_id,
                "NotificationURL": "https://casino.example.com/payments/trustly/notify",
                "SuccessURL": f"https://casino.example.com/payments/trustly/success/{payment.payment_id}",
                "FailURL": f"https://casino.example.com/payments/trustly/fail/{payment.payment_id}",
            },
            method_id=method_id,
        )
        try:
            resp = await self._http.post(self.settings.base_url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            result = data.get("result", {})
            if data.get("error"):
                err = data["error"]
                return self._failure_response(str(err.get("code")), err.get("message", ""))
            order_id = result.get("data", {}).get("orderid")
            redirect_url = result.get("data", {}).get("url")
            return PSPResponse(
                success=True,
                external_transaction_id=order_id,
                redirect_url=redirect_url,
                status=PaymentStatus.PENDING,
                raw_response=data,
            )
        except Exception as exc:
            logger.exception("Trustly deposit failed")
            return self._failure_response("TRUSTLY_ERROR", str(exc))

    async def get_transaction_status(self, external_id: str) -> PSPResponse:
        # Trustly is notification-driven; polling is a fallback
        return PSPResponse(
            success=False,
            external_transaction_id=external_id,
            status=PaymentStatus.PROCESSING,
            raw_response={},
            error_message="Trustly status is notification-driven",
        )

    async def withdraw(self, withdrawal: Withdrawal) -> PSPResponse:
        payload = self._build_rpc_request(
            method="AccountPayout",
            params={
                "Attributes": {
                    "Currency": withdrawal.currency,
                    "Amount": f"{withdrawal.amount / 100:.2f}",
                },
                "EndUserID": str(withdrawal.user_id),
                "MessageID": withdrawal.withdrawal_id,
                "NotificationURL": "https://casino.example.com/payments/trustly/notify",
            },
        )
        try:
            resp = await self._http.post(self.settings.base_url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            if data.get("error"):
                err = data["error"]
                return self._failure_response(str(err.get("code")), err.get("message", ""))
            order_id = data.get("result", {}).get("data", {}).get("orderid")
            return PSPResponse(
                success=True,
                external_transaction_id=order_id,
                status=PaymentStatus.PROCESSING,
                raw_response=data,
            )
        except Exception as exc:
            return self._failure_response("TRUSTLY_WITHDRAW_ERROR", str(exc))

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_rpc_request(
        self,
        method: str,
        params: dict[str, Any],
        method_id: Optional[str] = None,
    ) -> dict[str, Any]:
        return {
            "method": method,
            "params": {
                "Data": params,
                "Signature": {
                    "Method": method,
                    "UUID": method_id or str(uuid.uuid4()),
                    "Data": params,
                    # Real implementation signs with RSA private key
                    "Signature": "_placeholder_rsa_signature_",
                },
                "Credentials": {
                    "Username": self.settings.username,
                    "Password": self.settings.password,
                },
            },
            "version": "1.1",
        }

    async def close(self) -> None:
        await self._http.aclose()

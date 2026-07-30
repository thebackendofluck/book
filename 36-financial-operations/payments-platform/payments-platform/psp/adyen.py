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
Adyen PSP adapter.

Adyen is the primary card-processing provider.  This implementation covers:
  - Standard card deposits (redirect + direct integration)
  - Apple Pay / Google Pay via Adyen checkout
  - 3-D Secure (VERIFY state)
  - Refunds and voids
  - Webhook notification parsing

Docs: https://docs.adyen.com/api-explorer/
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from base64 import b64encode
from typing import Any, Optional

import httpx

from models import Deposit, PaymentStatus, PSPResponse, Withdrawal
from psp.base import PSPAdapter

logger = logging.getLogger(__name__)


class AdyenSettings:
    def __init__(
        self,
        api_key: str,
        merchant_account: str,
        base_url: str = "https://checkout-test.adyen.com/v71",
        notification_hmac_key: str = "",
        live_url_prefix: Optional[str] = None,
    ) -> None:
        self.api_key = api_key
        self.merchant_account = merchant_account
        self.base_url = base_url
        self.notification_hmac_key = notification_hmac_key
        self.live_url_prefix = live_url_prefix


class AdyenAdapter(PSPAdapter):
    """
    Full Adyen Checkout adapter.

    Amounts are in minor units (cents) — same as the internal model so no
    conversion is needed beyond passing the value through.
    """

    name = "adyen"
    supports_refunds = True
    supports_withdrawals = False

    def __init__(self, settings: AdyenSettings) -> None:
        self.settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.base_url,
            headers={
                "X-API-Key": settings.api_key,
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    # ------------------------------------------------------------------
    # PSPAdapter interface
    # ------------------------------------------------------------------

    async def deposit(self, payment: Deposit) -> PSPResponse:
        payload = self._build_payment_request(payment)
        try:
            resp = await self._client.post("/payments", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return self._parse_payment_response(payment.payment_id, data)
        except httpx.HTTPStatusError as exc:
            logger.error("Adyen HTTP error %s: %s", exc.response.status_code, exc.response.text)
            return self._failure_response(
                str(exc.response.status_code), exc.response.text
            )
        except Exception as exc:
            logger.exception("Adyen deposit failed")
            return self._failure_response("ADYEN_ERROR", str(exc))

    async def get_transaction_status(self, external_id: str) -> PSPResponse:
        try:
            resp = await self._client.get(f"/payments/{external_id}")
            resp.raise_for_status()
            data = resp.json()
            result_code = data.get("resultCode", "Unknown")
            status = _adyen_result_to_status(result_code)
            return PSPResponse(
                success=status == PaymentStatus.SUCCEEDED,
                external_transaction_id=external_id,
                status=status,
                raw_response=data,
            )
        except Exception as exc:
            logger.exception("Adyen status check failed for %s", external_id)
            return self._failure_response("STATUS_ERROR", str(exc))

    async def refund(self, external_id: str, amount: int, currency: str) -> PSPResponse:
        payload = {
            "merchantAccount": self.settings.merchant_account,
            "amount": {"currency": currency, "value": amount},
            "reference": f"refund-{external_id}",
        }
        try:
            resp = await self._client.post(
                f"/payments/{external_id}/refunds", json=payload
            )
            resp.raise_for_status()
            data = resp.json()
            return PSPResponse(
                success=True,
                external_transaction_id=data.get("pspReference"),
                status=PaymentStatus.REFUNDED,
                raw_response=data,
            )
        except Exception as exc:
            logger.exception("Adyen refund failed for %s", external_id)
            return self._failure_response("REFUND_ERROR", str(exc))

    async def void(self, external_id: str) -> PSPResponse:
        payload = {"merchantAccount": self.settings.merchant_account}
        try:
            resp = await self._client.post(
                f"/payments/{external_id}/cancels", json=payload
            )
            resp.raise_for_status()
            return PSPResponse(
                success=True,
                external_transaction_id=external_id,
                status=PaymentStatus.VOIDED,
                raw_response=resp.json(),
            )
        except Exception as exc:
            return self._failure_response("VOID_ERROR", str(exc))

    # ------------------------------------------------------------------
    # Webhook notification handling
    # ------------------------------------------------------------------

    def verify_notification_hmac(
        self, notification_items: list[dict[str, Any]]
    ) -> bool:
        """Verify Adyen HMAC signature on incoming webhook notifications."""
        if not self.settings.notification_hmac_key:
            logger.warning("HMAC key not configured — skipping notification verification")
            return True
        for item in notification_items:
            nd = item.get("NotificationRequestItem", {})
            sig = nd.pop("additionalData", {}).get("hmacSignature", "")
            expected = self._compute_hmac(nd)
            if not hmac.compare_digest(sig, expected):
                return False
        return True

    def parse_notification(self, item: dict[str, Any]) -> PSPResponse:
        """Convert an Adyen webhook notification into a PSPResponse."""
        event_code = item.get("eventCode", "")
        success = item.get("success", "false").lower() == "true"
        psp_ref = item.get("pspReference", "")
        status = _adyen_event_to_status(event_code, success)
        return PSPResponse(
            success=success,
            external_transaction_id=psp_ref,
            status=status,
            raw_response=item,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_payment_request(self, payment: Deposit) -> dict[str, Any]:
        return {
            "merchantAccount": self.settings.merchant_account,
            "amount": {
                "currency": payment.psp_currency,
                "value": payment.psp_amount,
            },
            "reference": payment.payment_id,
            "paymentMethod": {"type": payment.method.value},
            "returnUrl": f"https://casino.example.com/payments/adyen/return/{payment.payment_id}",
            "shopperIP": payment.user_ip,
            "channel": "Web",
            "additionalData": {"allow3DS2": "true"},
        }

    def _parse_payment_response(
        self, payment_id: str, data: dict[str, Any]
    ) -> PSPResponse:
        result_code = data.get("resultCode", "Unknown")
        status = _adyen_result_to_status(result_code)
        return PSPResponse(
            success=result_code in {"Authorised", "Pending"},
            external_transaction_id=data.get("pspReference"),
            redirect_url=data.get("action", {}).get("url"),
            status=status,
            raw_response=data,
            error_code=data.get("refusalReasonCode"),
            error_message=data.get("refusalReason"),
        )

    def _compute_hmac(self, data: dict[str, Any]) -> str:
        signing_string = ":".join(
            str(data.get(k, "")) for k in sorted(data.keys())
        )
        key = bytes.fromhex(self.settings.notification_hmac_key)
        hm = hmac.new(key, signing_string.encode("utf-8"), hashlib.sha256)
        return b64encode(hm.digest()).decode()

    async def close(self) -> None:
        await self._client.aclose()


# ------------------------------------------------------------------
# Mapping helpers
# ------------------------------------------------------------------


def _adyen_result_to_status(result_code: str) -> PaymentStatus:
    mapping = {
        "Authorised": PaymentStatus.SUCCEEDED,
        "Pending": PaymentStatus.PENDING,
        "Received": PaymentStatus.PROCESSING,
        "RedirectShopper": PaymentStatus.VERIFY,
        "IdentifyShopper": PaymentStatus.VERIFY,
        "ChallengeShopper": PaymentStatus.VERIFY,
        "PresentToShopper": PaymentStatus.PENDING,
        "Refused": PaymentStatus.FAILED,
        "Cancelled": PaymentStatus.VOIDED,
        "Error": PaymentStatus.FAILED,
    }
    return mapping.get(result_code, PaymentStatus.FAILED)


def _adyen_event_to_status(event_code: str, success: bool) -> PaymentStatus:
    if not success:
        return PaymentStatus.FAILED
    mapping = {
        "AUTHORISATION": PaymentStatus.SUCCEEDED,
        "CANCELLATION": PaymentStatus.VOIDED,
        "REFUND": PaymentStatus.REFUNDED,
        "PENDING": PaymentStatus.PENDING,
    }
    return mapping.get(event_code, PaymentStatus.PROCESSING)

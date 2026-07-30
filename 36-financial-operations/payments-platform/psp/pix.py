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
PIX PSP adapter (Brazilian instant payments).

PIX is the Central Bank of Brazil's instant payment rail.  Deposits
are initiated by generating a QR code / copy-paste key; the player
pays through their bank app and a webhook confirms the transfer.

This adapter wraps a generic PIX aggregator API (e.g. Gerencianet /
Efí Bank, PagSeguro, Mercado Pago).

Docs: https://developers.efipay.com.br/docs/pix-introduction
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from models import Deposit, PaymentStatus, PSPResponse
from psp.base import PSPAdapter

logger = logging.getLogger(__name__)


class PixSettings:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        pix_key: str,                  # chave PIX (CPF, CNPJ, phone, e-mail, or random key)
        sandbox: bool = True,
        base_url: str = "https://pix.example.com",
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.pix_key = pix_key
        self.sandbox = sandbox
        self.base_url = base_url


class PixAdapter(PSPAdapter):
    """
    PIX instant payment adapter.

    Deposits generate a QR code.  Withdrawals (payouts) use the PIX
    wire transfer (TED / PIX direto).
    """

    name = "pix"
    supports_refunds = True
    supports_withdrawals = False

    def __init__(self, settings: PixSettings) -> None:
        self.settings = settings
        self._access_token: Optional[str] = None
        self._http = httpx.AsyncClient(base_url=settings.base_url, timeout=30.0)

    # ------------------------------------------------------------------
    # PSPAdapter interface
    # ------------------------------------------------------------------

    async def deposit(self, payment: Deposit) -> PSPResponse:
        token = await self._get_access_token()
        payload = {
            "calendario": {"expiracao": 3600},
            "devedor": {"cpf": payment.metadata.get("cpf", ""), "nome": payment.metadata.get("name", "")},
            "valor": {"original": f"{payment.psp_amount / 100:.2f}"},
            "chave": self.settings.pix_key,
            "infoAdicionais": [
                {"nome": "paymentId", "valor": payment.payment_id}
            ],
        }
        try:
            resp = await self._http.put(
                f"/v2/cob/{payment.payment_id[:35]}",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            data = resp.json()
            qr_code = data.get("pixCopiaECola", "")
            return PSPResponse(
                success=True,
                external_transaction_id=data.get("txid"),
                redirect_url=None,
                status=PaymentStatus.PENDING,
                raw_response=data,
                # QR code string returned as metadata field
            )
        except Exception as exc:
            logger.exception("PIX deposit initiation failed")
            return self._failure_response("PIX_ERROR", str(exc))

    async def get_transaction_status(self, external_id: str) -> PSPResponse:
        token = await self._get_access_token()
        try:
            resp = await self._http.get(
                f"/v2/cob/{external_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            data = resp.json()
            status_str = data.get("status", "ATIVA")
            status = _pix_status_to_payment_status(status_str)
            return PSPResponse(
                success=status == PaymentStatus.SUCCEEDED,
                external_transaction_id=external_id,
                status=status,
                raw_response=data,
            )
        except Exception as exc:
            return self._failure_response("PIX_STATUS_ERROR", str(exc))

    async def refund(self, external_id: str, amount: int, currency: str) -> PSPResponse:
        """PIX devolution (devolução)."""
        token = await self._get_access_token()
        refund_id = f"dev-{external_id[:26]}"
        payload = {"valor": f"{amount / 100:.2f}"}
        try:
            resp = await self._http.put(
                f"/v2/pix/{external_id}/devolucao/{refund_id}",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            return PSPResponse(
                success=True,
                external_transaction_id=refund_id,
                status=PaymentStatus.REFUNDED,
                raw_response=resp.json(),
            )
        except Exception as exc:
            return self._failure_response("PIX_REFUND_ERROR", str(exc))

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _get_access_token(self) -> str:
        if self._access_token:
            return self._access_token
        resp = await self._http.post(
            "/oauth/token",
            data={"grant_type": "client_credentials"},
            auth=(self.settings.client_id, self.settings.client_secret),
        )
        resp.raise_for_status()
        self._access_token = resp.json()["access_token"]
        return self._access_token  # type: ignore[return-value]

    async def close(self) -> None:
        await self._http.aclose()


def _pix_status_to_payment_status(status: str) -> PaymentStatus:
    mapping = {
        "ATIVA": PaymentStatus.PENDING,
        "CONCLUIDA": PaymentStatus.SUCCEEDED,
        "REMOVIDA_PELO_USUARIO_RECEBEDOR": PaymentStatus.VOIDED,
        "REMOVIDA_PELO_PSP": PaymentStatus.VOIDED,
    }
    return mapping.get(status, PaymentStatus.PENDING)

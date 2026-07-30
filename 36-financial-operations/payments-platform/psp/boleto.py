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
Boleto PSP adapter (Brazilian cash / bank slip payments).

Boleto deposits are initiated by issuing a boleto with a barcode / digitable
line. The player completes payment at their bank or lottery agent and the
provider later confirms settlement via callback.
"""

from __future__ import annotations

import logging

from models import Deposit, PSPResponse, PaymentStatus
from psp.base import PSPAdapter

logger = logging.getLogger(__name__)


class BoletoAdapter(PSPAdapter):
    """Minimal boleto adapter used by the chapter runtime."""

    name = "boleto"
    supports_refunds = False
    supports_withdrawals = False

    async def deposit(self, payment: Deposit) -> PSPResponse:
        digitable_line = f"34191{payment.payment_id[:10].upper():0<10}0000000000000"
        return PSPResponse(
            success=True,
            external_transaction_id=f"BOL-{payment.payment_id[:12]}",
            status=PaymentStatus.PENDING,
            raw_response={
                "provider": "boleto",
                "barcode": digitable_line,
                "digitable_line": digitable_line,
                "expires_in_days": 3,
            },
        )

    async def get_transaction_status(self, external_id: str) -> PSPResponse:
        logger.info("boleto.status_poll external_id=%s", external_id)
        return PSPResponse(
            success=False,
            external_transaction_id=external_id,
            status=PaymentStatus.PENDING,
            raw_response={"provider": "boleto"},
            error_message="Boleto settlement is callback-driven",
        )

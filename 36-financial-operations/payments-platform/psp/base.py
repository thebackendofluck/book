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
Abstract base class for all PSP adapters.

Every PSP integration must implement this interface so the PSP router
can treat all providers uniformly.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Optional

from models import Deposit, PSPResponse, PaymentStatus, Withdrawal

logger = logging.getLogger(__name__)


class PSPAdapter(ABC):
    """
    Common interface for payment service provider integrations.

    Subclasses must implement at minimum `deposit` and `get_transaction_status`.
    Withdrawal support is optional — raise NotImplementedError if unsupported.
    """

    name: str = "base"
    supports_refunds: bool = False
    supports_withdrawals: bool = False

    @abstractmethod
    async def deposit(self, payment: Deposit) -> PSPResponse:
        """Initiate a deposit with the PSP. Returns a normalised PSPResponse."""
        ...

    @abstractmethod
    async def get_transaction_status(self, external_id: str) -> PSPResponse:
        """Poll the PSP for the current status of a transaction."""
        ...

    async def refund(self, external_id: str, amount: int, currency: str) -> PSPResponse:
        raise NotImplementedError(f"{self.name} does not support refunds")

    async def withdraw(self, withdrawal: Withdrawal) -> PSPResponse:
        raise NotImplementedError(f"{self.name} does not support withdrawals")

    async def void(self, external_id: str) -> PSPResponse:
        raise NotImplementedError(f"{self.name} does not support voids")

    def health_check(self) -> bool:
        """Lightweight check used by the PSP router. Override for real HTTP ping."""
        return True

    def _failure_response(self, code: str, message: str) -> PSPResponse:
        return PSPResponse(
            success=False,
            status=PaymentStatus.FAILED,
            error_code=code,
            error_message=message,
        )

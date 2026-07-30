# Companion code for "The Backend of Luck" - Chapter 10, Complete Platform Architecture.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
suppliers/kambi/provider.py
----------------------------
Kambi — Sportsbook fund/withdraw integration.

Integration model
-----------------
Kambi uses a **fund/withdraw** wallet model (distinct from casino's
seamless-wallet). In this model:
- When a player places a bet, Kambi calls the operator's `/fund` endpoint
  to debit the player's wallet.
- When the bet settles, Kambi calls `/withdraw` to credit the winnings.
- Kambi holds no player funds; all money stays in the operator's wallet.

This is different from the typical casino integration where the supplier
holds the session balance. The sportsbook fund/withdraw model means every
single bet placement requires a real-time API call.

Quirks and gotchas
------------------
1. **Kambi transaction ID** — Kambi's `kambiTransactionId` is a long
   integer. It must be stored and echoed back in responses. The platform
   uses this as the `supplier_ref` for idempotency.

2. **FundRequest vs WithdrawRequest** — "Fund" in Kambi's terminology is
   a debit (money flows FROM the player TO Kambi to cover the bet stake).
   "Withdraw" is a credit (money flows FROM Kambi TO the player as winnings).
   This naming is from Kambi's perspective, which is the opposite of the
   operator's perspective. Take care not to confuse the direction.

3. **Combination structure** — A single coupon can contain multiple
   combinations (legs of an accumulator). Each combination has its own
   stake. The `betInformation.combinations` list represents these legs.

4. **Session token** — Kambi's `playerSessionToken` is the operator-issued
   game token. It must be validated on every request.

5. **Product types** — Kambi supports SPORTSBOOK, VIRTUAL_SPORTS, and
   NUMBERS. The `productType` field identifies which product the bet is for.
   Different products may have different wagering treatments.

6. **Regulation IDs** — Kambi passes a `regulationId` on authentication.
   This maps to the jurisdiction and determines applicable limits.

7. **Concurrent bets** — Kambi can call fund multiple times before a
   withdraw if a player places several bets quickly. Each has a unique
   `kambiTransactionId`.

API reference: https://docs.kambi.com/operator-api/
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional

import httpx

from accounts_provider import (
    CreditOperation,
    DebitOperation,
    PlayerSession,
    RefundOperation,
    SupplierOperation,
    TransactionContext,
)
from transaction_result import (
    BalanceStatus,
    TransactionResult,
    TransactionType,
    success_result,
)

logger = logging.getLogger(__name__)

SUPPLIER_ID = "kambi"


# ---------------------------------------------------------------------------
# Kambi protocol models (simplified from KambiProtocol.scala)
# ---------------------------------------------------------------------------


@dataclass
class KambiCombination:
    combination_ref: int
    size: int
    live_betting: bool
    stake: Decimal
    odds: Decimal


@dataclass
class KambiFundRequest:
    """Debit request — player places a bet."""
    customer_player_id: str
    kambi_transaction_id: str
    kambi_transaction_type: str
    player_session_token: str
    product_type: str
    currency_code: str
    amount: Optional[Decimal]
    combinations: List[KambiCombination]


@dataclass
class KambiWithdrawRequest:
    """Credit request — bet settled, winnings paid."""
    customer_player_id: str
    kambi_transaction_id: str
    kambi_transaction_type: str
    player_session_token: str
    product_type: str
    currency_code: str
    amount: Optional[Decimal]


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class KambiProvider:
    """
    AccountsProvider for Kambi sportsbook.

    Implements the operator-side wallet service that Kambi calls for
    fund (debit) and withdraw (credit) operations.
    """

    def __init__(
        self,
        operator_id: str,
        market_id: str,
        http_timeout_s: float = 3.0,
    ) -> None:
        self._operator_id = operator_id
        self._market_id = market_id
        self._http_client = httpx.AsyncClient(timeout=http_timeout_s)

    async def authenticate(self, token: str) -> PlayerSession:
        """
        Authenticate a Kambi ticket (session token).

        Kambi calls this as the `/authenticate` endpoint with the player's
        session token. The operator validates and returns player details
        including currency, country, and locale.

        The `regulationId` in the response determines which limit rules apply.
        """
        logger.debug("Kambi authenticate token=%s...", token[:16])
        from transaction_result import AuthenticationError
        raise AuthenticationError("Not implemented: validate Kambi session token")

    async def get_balance(
        self, session: PlayerSession, game_id: Optional[str] = None
    ) -> BalanceStatus:
        """
        Return player balance for Kambi's initial balance check.

        Kambi checks balance during session authentication. Balance is
        returned in major units with currency precision.
        """
        logger.debug("Kambi get_balance player=%s", session.player_id)
        return BalanceStatus(
            cash_balance=Decimal("0"),
            bonus_balance=Decimal("0"),
            currency=session.currency or "GBP",
        )

    async def debit(
        self,
        session: PlayerSession,
        operation: DebitOperation,
        context: TransactionContext,
    ) -> TransactionResult:
        """
        Handle Kambi FUND request.

        A FUND is called when the player confirms a bet slip. The amount
        is the total stake across all combinations on the coupon.

        Important: Kambi calls this BEFORE the bet is confirmed in their
        system. If the debit fails, Kambi will not place the bet.
        """
        logger.info(
            "Kambi FUND player=%s tx=%s amount=%s product=%s",
            session.player_id, context.supplier_ref,
            operation.amount, getattr(context, "product_type", "SPORTSBOOK"),
        )
        return await self.apply_transaction(session, [operation], context)

    async def credit(
        self,
        session: PlayerSession,
        operation: CreditOperation,
        context: TransactionContext,
    ) -> TransactionResult:
        """
        Handle Kambi WITHDRAW request.

        A WITHDRAW is called when a bet settles. The amount is the net
        return (stake + winnings for a win, 0 for a loss — but Kambi still
        calls withdraw for 0-amount settlements to close the round).
        """
        logger.info(
            "Kambi WITHDRAW player=%s tx=%s amount=%s",
            session.player_id, context.supplier_ref, operation.amount,
        )
        return await self.apply_transaction(session, [operation], context)

    async def refund(
        self,
        session: PlayerSession,
        operation: RefundOperation,
        context: TransactionContext,
    ) -> TransactionResult:
        """
        Handle Kambi ROLLBACK — reverse a fund for a void/cancelled bet.

        Kambi calls ROLLBACK when a bet is voided post-placement (e.g.
        match cancelled, technical error). The fund amount is returned.
        """
        logger.info(
            "Kambi ROLLBACK player=%s tx=%s",
            session.player_id, context.supplier_ref,
        )
        return await self.reverse_transaction(session, [operation], context)

    async def apply_transaction(
        self,
        session: PlayerSession,
        operations: list[SupplierOperation],
        context: TransactionContext,
    ) -> TransactionResult:
        """Apply Kambi wallet operations."""
        total_debit = sum((op.amount for op in operations if isinstance(op, DebitOperation)), Decimal("0"))
        total_credit = sum((op.amount for op in operations if isinstance(op, CreditOperation)), Decimal("0"))

        balance = BalanceStatus(
            cash_balance=Decimal("0"),
            bonus_balance=Decimal("0"),
            currency=session.currency or "GBP",
        )
        tx_type = TransactionType.DEBIT if total_debit > total_credit else TransactionType.CREDIT
        return success_result(
            tx_type=tx_type,
            balance=balance,
            tx_id=context.tx_id,
            external_id=context.supplier_ref,
            cash_usage=total_debit,
        )

    async def reverse_transaction(
        self,
        session: PlayerSession,
        operations: list[SupplierOperation],
        context: TransactionContext,
    ) -> TransactionResult:
        """Reverse a Kambi fund (void/rollback)."""
        balance = BalanceStatus(
            cash_balance=Decimal("0"),
            bonus_balance=Decimal("0"),
            currency=session.currency or "GBP",
        )
        return success_result(
            tx_type=TransactionType.REFUND,
            balance=balance,
            tx_id=context.tx_id,
            external_id=context.supplier_ref,
        )

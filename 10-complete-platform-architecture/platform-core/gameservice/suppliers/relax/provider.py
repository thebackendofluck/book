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
suppliers/relax/provider.py
----------------------------
Relax Gaming — Content Aggregator seamless-wallet integration.

Integration model
-----------------
Relax Gaming is a content aggregator that distributes games from multiple
studios (Hacksaw, Nolimit City, Push Gaming, etc.) under a single technical
integration. The operator integrates once with Relax and gains access to
all studios in their portfolio.

Relax uses a seamless-wallet model with a REST JSON API.

Quirks and gotchas
------------------
1. **Studio-level game IDs** — Relax aggregates games from many studios.
   The `gameRef` field identifies the underlying studio game (e.g.
   "hacksaw:SupportYourKing"). The operator may need studio-specific
   logic based on the gameRef prefix.

2. **Aggregator vs direct** — Some studios (e.g. Push Gaming) offer direct
   integration AND aggregated delivery through Relax. If a studio is
   integrated directly, you must route based on the game launch URL, not
   the studio name, to avoid double-processing.

3. **Currency conversion** — Relax sends amounts in the player's native
   currency. For currencies with no minor-unit convention (e.g. JPY),
   the amount field has no decimal component.

4. **Bonus buy** — Relax supports "bonus buy" (direct entry to bonus round).
   These generate a single large DEBIT with `betType: BONUS_BUY`. The
   credit follows as a normal WIN when the bonus round ends.

5. **Turbo spin** — Relax passes `turboSpin: true` for fast-spin modes.
   This has no wallet impact but may trigger different limits in some
   jurisdictions.

6. **Session refresh** — Relax periodically sends a BALANCE request to
   refresh the session. Respond with the current balance to keep the
   session alive.

7. **Transaction reference** — Relax uses a UUID `transactionRef` as the
   idempotency key. This maps to `supplier_ref` in the GAL.

API reference: https://relax-gaming.atlassian.net/wiki/
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

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

SUPPLIER_ID = "relax"

# Relax bet types
BET_TYPE_REGULAR = "REGULAR"
BET_TYPE_BONUS_BUY = "BONUS_BUY"
BET_TYPE_FREE_ROUND = "FREE_ROUND"

# Relax transaction types
RELAX_BET = "BET"
RELAX_WIN = "WIN"
RELAX_REFUND = "REFUND"
RELAX_BALANCE = "BALANCE"


class RelaxProvider:
    """
    AccountsProvider for Relax Gaming aggregator.

    A single integration covers all studios in Relax's portfolio.
    Studio-specific handling (e.g. Hacksaw crash games) is done based
    on the gameRef field in the callback.
    """

    def __init__(
        self,
        operator_id: str,
        api_key: str,
        http_timeout_s: float = 5.0,
    ) -> None:
        self._operator_id = operator_id
        self._api_key = api_key
        self._http_client = httpx.AsyncClient(timeout=http_timeout_s)

    async def authenticate(self, token: str) -> PlayerSession:
        """
        Validate a Relax session token.

        Relax uses UUID-format session tokens issued by the operator
        at game launch. Validate against the session store.
        """
        logger.debug("Relax authenticate token=%s...", token[:16])
        from transaction_result import AuthenticationError
        raise AuthenticationError("Not implemented: validate Relax session token")

    async def get_balance(
        self, session: PlayerSession, game_id: Optional[str] = None
    ) -> BalanceStatus:
        """
        Return balance for Relax's BALANCE request.

        Relax periodically calls BALANCE to refresh the session and
        display the current wallet amount in the game UI.
        """
        logger.debug("Relax get_balance player=%s", session.player_id)
        return BalanceStatus(
            cash_balance=Decimal("0"),
            bonus_balance=Decimal("0"),
            currency=session.currency or "EUR",
        )

    async def debit(
        self,
        session: PlayerSession,
        operation: DebitOperation,
        context: TransactionContext,
    ) -> TransactionResult:
        """Handle Relax BET callback."""
        logger.info(
            "Relax BET player=%s round=%s amount=%s",
            session.player_id, operation.round_id, operation.amount,
        )
        return await self.apply_transaction(session, [operation], context)

    async def credit(
        self,
        session: PlayerSession,
        operation: CreditOperation,
        context: TransactionContext,
    ) -> TransactionResult:
        """Handle Relax WIN callback."""
        logger.info(
            "Relax WIN player=%s round=%s amount=%s",
            session.player_id, operation.round_id, operation.amount,
        )
        return await self.apply_transaction(session, [operation], context)

    async def refund(
        self,
        session: PlayerSession,
        operation: RefundOperation,
        context: TransactionContext,
    ) -> TransactionResult:
        """Handle Relax REFUND callback."""
        logger.info(
            "Relax REFUND player=%s round=%s",
            session.player_id, operation.round_id,
        )
        return await self.reverse_transaction(session, [operation], context)

    async def apply_transaction(
        self,
        session: PlayerSession,
        operations: list[SupplierOperation],
        context: TransactionContext,
    ) -> TransactionResult:
        """Apply Relax wallet operations."""
        total_debit = sum((op.amount for op in operations if isinstance(op, DebitOperation)), Decimal("0"))
        total_credit = sum((op.amount for op in operations if isinstance(op, CreditOperation)), Decimal("0"))

        balance = BalanceStatus(
            cash_balance=Decimal("0"),
            bonus_balance=Decimal("0"),
            currency=session.currency or "EUR",
        )
        tx_type = TransactionType.DEBIT if total_debit >= total_credit else TransactionType.CREDIT
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
        """Reverse a Relax transaction."""
        balance = BalanceStatus(
            cash_balance=Decimal("0"),
            bonus_balance=Decimal("0"),
            currency=session.currency or "EUR",
        )
        return success_result(
            tx_type=TransactionType.REFUND,
            balance=balance,
            tx_id=context.tx_id,
            external_id=context.supplier_ref,
        )

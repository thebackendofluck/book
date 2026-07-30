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
suppliers/igt/provider.py
--------------------------
IGT (International Game Technology) — Land-based crossover integration.

Integration model
-----------------
IGT is primarily a land-based machine manufacturer (slot cabinets, lottery)
that has expanded into online. Their online integration bridges their
land-based game math with a web wallet interface.

IGT offers two integration paths:
1. **IGT Connect** — Legacy SOAP-based wallet service.
2. **GambetDC / FortuNet** — Modern REST API for US markets.

This provider implements the modern REST variant.

Quirks and gotchas
------------------
1. **Land-based game math** — IGT games are often certified for land-based
   play first. The RTP (return-to-player) may differ between the land-based
   and online variants of the same game title. Check the certificate.

2. **Weighted random number generation** — IGT uses a hardware-certified
   RNG on physical machines. Online games use a software equivalent that
   must be independently certified in each jurisdiction. The certification
   certificate number appears in the game's launch URL.

3. **Persistent state** — Some IGT games (e.g. Fortune Coin) have
   persistent state (accumulating jackpot meters, carry-over bonuses).
   Wallet operations for persistent state use different action codes
   than regular spin results.

4. **Jurisdictional variants** — IGT maintains separate game builds for
   different jurisdictions (UK, Malta, New Jersey, etc.). The game ID
   includes a jurisdiction suffix (e.g. `ELFKINGUK` vs `ELFKINGMT`).
   Route to the correct build based on the player's jurisdiction.

5. **Session model** — IGT uses a machine-session model inherited from
   land-based. The "machine ID" in their protocol corresponds to the
   game session ID. Sessions are tied to a physical or virtual cabinet.

6. **Meter reconciliation** — Land-based IGT integrations require periodic
   meter reconciliation (comparing the machine's accounting meters against
   the operator's records). Online deployments replace this with the GAL
   transaction log.

7. **Legacy currency handling** — IGT's legacy protocol sends amounts in
   "credits" where 1 credit = denomination × number_of_credits. Online
   integrations use standard currency amounts.

API reference: Internal IGT Connect API documentation (NDA-protected).
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

SUPPLIER_ID = "igt"

# IGT result codes (FortuNet/online REST variant)
IGT_OK = "SUCCESS"
IGT_INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
IGT_INVALID_SESSION = "INVALID_SESSION"
IGT_DUPLICATE_TRANSACTION = "DUPLICATE_TRANSACTION"
IGT_GAME_NOT_FOUND = "GAME_NOT_FOUND"
IGT_INTERNAL_ERROR = "INTERNAL_ERROR"


class IGTProvider:
    """
    AccountsProvider for IGT online games (FortuNet/IGT Connect REST).

    Supports both RNG slot games and progressive jackpot titles.
    Land-based crossover games require jurisdiction-specific routing.
    """

    def __init__(
        self,
        api_base_url: str,
        operator_id: str,
        api_key: str,
        jurisdiction: str = "MT",
        http_timeout_s: float = 5.0,
    ) -> None:
        self._api_base_url = api_base_url.rstrip("/")
        self._operator_id = operator_id
        self._api_key = api_key
        self._jurisdiction = jurisdiction
        self._http_client = httpx.AsyncClient(
            timeout=http_timeout_s,
            headers={
                "X-Operator-ID": operator_id,
                "X-API-Key": api_key,
                "Content-Type": "application/json",
            },
        )

    async def authenticate(self, token: str) -> PlayerSession:
        """
        Validate an IGT game session token.

        IGT's machine-session model maps the token to a virtual cabinet
        session. The token is validated against the session store and the
        associated jurisdiction is checked for game availability.
        """
        logger.debug("IGT authenticate token=%s...", token[:16])
        from transaction_result import AuthenticationError
        raise AuthenticationError("Not implemented: validate IGT session token")

    async def get_balance(
        self, session: PlayerSession, game_id: Optional[str] = None
    ) -> BalanceStatus:
        """
        Return balance for IGT's balance inquiry.

        IGT uses credits internally but the online REST API accepts and
        returns standard currency amounts. No conversion needed for
        online games.
        """
        logger.debug("IGT get_balance player=%s jurisdiction=%s", session.player_id, self._jurisdiction)
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
        Handle IGT bet/debit transaction.

        For progressive jackpot games, IGT may deduct an additional
        jackpot contribution alongside the bet. This appears as a
        separate operation in the callback.
        """
        logger.info(
            "IGT DEBIT player=%s round=%s amount=%s jurisdiction=%s",
            session.player_id, operation.round_id, operation.amount, self._jurisdiction,
        )
        return await self.apply_transaction(session, [operation], context)

    async def credit(
        self,
        session: PlayerSession,
        operation: CreditOperation,
        context: TransactionContext,
    ) -> TransactionResult:
        """Handle IGT win/credit transaction."""
        logger.info(
            "IGT CREDIT player=%s round=%s amount=%s",
            session.player_id, operation.round_id, operation.amount,
        )
        return await self.apply_transaction(session, [operation], context)

    async def refund(
        self,
        session: PlayerSession,
        operation: RefundOperation,
        context: TransactionContext,
    ) -> TransactionResult:
        """Handle IGT rollback (incomplete round)."""
        logger.info(
            "IGT REFUND player=%s round=%s",
            session.player_id, operation.round_id,
        )
        return await self.reverse_transaction(session, [operation], context)

    async def apply_transaction(
        self,
        session: PlayerSession,
        operations: list[SupplierOperation],
        context: TransactionContext,
    ) -> TransactionResult:
        """Apply IGT wallet operations."""
        total_debit = sum((op.amount for op in operations if isinstance(op, DebitOperation)), Decimal("0"))
        total_credit = sum((op.amount for op in operations if isinstance(op, CreditOperation)), Decimal("0"))

        balance = BalanceStatus(
            cash_balance=Decimal("0"),
            bonus_balance=Decimal("0"),
            currency=session.currency or "GBP",
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
        """Reverse an IGT transaction."""
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

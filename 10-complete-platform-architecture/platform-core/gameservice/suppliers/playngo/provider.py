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
suppliers/playngo/provider.py
------------------------------
Play'n GO — Slots seamless-wallet integration.

Integration model
-----------------
Play'n GO uses a seamless-wallet integration where the operator hosts
a SOAP or REST wallet service. Play'n GO calls the operator's endpoints
for authentication, balance, debit, and credit operations.

Quirks and gotchas
------------------
1. **CasinoService SOAP** — Play'n GO's legacy integration uses SOAP/XML.
   This provider implements the REST/JSON variant introduced in 2019.
   Some operator agreements still require SOAP support; check your contract.

2. **Session token format** — Play'n GO's session token is a UUID-v4 string.
   The operator must maintain a mapping from these tokens to player sessions.
   Tokens expire after the configured session lifetime (default: 30 minutes
   of inactivity).

3. **Bet + Win in separate requests** — Unlike Evolution's combined
   WITHDRAW_AND_DEPOSIT, Play'n GO always sends separate bet and win
   callbacks. This means you will always see two requests per round.

4. **Partial refunds** — Play'n GO supports partial bet refunds for
   cancelled rounds where a partial win has already been paid. The refund
   amount may be less than the original bet.

5. **Free rounds** — Play'n GO's free-round bets have a `freeGamePhase`
   flag. The amount is always 0 for the bet; the win carries the award.

6. **Result code mapping** — Play'n GO expects HTTP 200 with a result code:
   0 = OK, 1 = User not found, 2 = Insufficient funds,
   3 = Transaction not found, 4 = Transaction already exists,
   5 = Too many requests

7. **Request logging** — Play'n GO requires the operator to log ALL
   requests and responses for audit purposes (regulatory requirement).
   The bridge audit log satisfies this requirement.

API reference: https://devcode.playngo.com/integration/
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
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

SUPPLIER_ID = "playngo"

# Play'n GO result codes
PNG_OK = 0
PNG_USER_NOT_FOUND = 1
PNG_INSUFFICIENT_FUNDS = 2
PNG_TRANSACTION_NOT_FOUND = 3
PNG_TRANSACTION_ALREADY_EXISTS = 4
PNG_TOO_MANY_REQUESTS = 5


class PlaynGoProvider:
    """
    AccountsProvider for Play'n GO slots.

    Implements the REST wallet service specification. Validates incoming
    requests using HMAC-SHA256 and delegates to the platform wallet.
    """

    def __init__(
        self,
        operator_api_url: str,
        operator_id: str,
        secret_key: str,
        http_timeout_s: float = 5.0,
    ) -> None:
        self._operator_api_url = operator_api_url.rstrip("/")
        self._operator_id = operator_id
        self._secret_key = secret_key
        self._http_client = httpx.AsyncClient(timeout=http_timeout_s)

    def verify_signature(self, token: str, tx_id: str, received_sig: str) -> bool:
        """
        Verify HMAC-SHA256 signature on Play'n GO callbacks.

        Play'n GO signs each request as:
        HMAC-SHA256(sessionToken + transactionId, operatorSecret)
        """
        expected = _hmac.new(
            self._secret_key.encode(),
            f"{token}{tx_id}".encode(),
            hashlib.sha256,
        ).hexdigest()
        return _hmac.compare_digest(received_sig.lower(), expected.lower())

    async def authenticate(self, token: str) -> PlayerSession:
        """
        Validate a Play'n GO session token.

        Play'n GO calls the operator's `/PlayerInfo` endpoint on game load.
        The operator validates the session token and returns player details.
        """
        logger.debug("Play'n GO authenticate token=%s...", token[:16])
        from transaction_result import AuthenticationError
        raise AuthenticationError("Not implemented: validate Play'n GO session token")

    async def get_balance(
        self, session: PlayerSession, game_id: Optional[str] = None
    ) -> BalanceStatus:
        """Return player balance. Play'n GO expects major units."""
        logger.debug("Play'n GO get_balance player=%s", session.player_id)
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
        """Handle Play'n GO Deposit callback (bet placed = funds leave player wallet)."""
        # NOTE: Play'n GO uses "Deposit" for the operator perspective (player deposits
        # money into the game) — this is a DEBIT from the player wallet's perspective.
        logger.info(
            "Play'n GO DEPOSIT(bet) player=%s round=%s amount=%s",
            session.player_id, operation.round_id, operation.amount,
        )
        return await self.apply_transaction(session, [operation], context)

    async def credit(
        self,
        session: PlayerSession,
        operation: CreditOperation,
        context: TransactionContext,
    ) -> TransactionResult:
        """Handle Play'n GO Withdraw callback (win paid = funds return to player wallet)."""
        # NOTE: Play'n GO uses "Withdraw" for the operator perspective (player withdraws
        # winnings from the game) — this is a CREDIT to the player wallet.
        logger.info(
            "Play'n GO WITHDRAW(win) player=%s round=%s amount=%s",
            session.player_id, operation.round_id, operation.amount,
        )
        return await self.apply_transaction(session, [operation], context)

    async def refund(
        self,
        session: PlayerSession,
        operation: RefundOperation,
        context: TransactionContext,
    ) -> TransactionResult:
        """Handle Play'n GO Rollback callback."""
        logger.info(
            "Play'n GO ROLLBACK player=%s round=%s",
            session.player_id, operation.round_id,
        )
        return await self.reverse_transaction(session, [operation], context)

    async def apply_transaction(
        self,
        session: PlayerSession,
        operations: list[SupplierOperation],
        context: TransactionContext,
    ) -> TransactionResult:
        """Apply Play'n GO wallet operations."""
        total_debit = sum((op.amount for op in operations if isinstance(op, DebitOperation)), Decimal("0"))
        total_credit = sum((op.amount for op in operations if isinstance(op, CreditOperation)), Decimal("0"))

        balance = BalanceStatus(
            cash_balance=Decimal("0"),
            bonus_balance=Decimal("0"),
            currency=session.currency or "EUR",
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
        """Reverse a Play'n GO transaction."""
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

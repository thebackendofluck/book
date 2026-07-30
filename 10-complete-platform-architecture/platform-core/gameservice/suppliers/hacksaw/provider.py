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
suppliers/hacksaw/provider.py
------------------------------
Hacksaw Gaming — Crash games and instant-win seamless-wallet integration.

Integration model
-----------------
Hacksaw Gaming specialises in crash games (multiplier-based instant games
like mines, plinko, crash) and high-volatility slots. They use a standard
seamless-wallet model with REST/JSON callbacks.

Hacksaw is distributed both directly and through Relax Gaming aggregation.
This provider handles the direct integration.

Quirks and gotchas
------------------
1. **Crash game rounds** — Crash games differ from slots in that the player
   can cash out at any multiplier before the crash. This generates:
   - DEBIT at bet placement
   - CREDIT at cash-out OR when round ends (if player didn't cash out: amount=0)
   Hacksaw ALWAYS sends a credit, even for losing rounds (with amount=0).
   Do NOT treat a 0-amount credit as suspicious — it's closing the round.

2. **Multiplayer crash** — Hacksaw's crash games are often multiplayer.
   Multiple players bet on the same round. The round_id is shared across
   all players in that round. The supplier_ref (transaction ID) is
   per-player, per-round.

3. **Provably fair** — Hacksaw publishes seed hashes for crash game outcomes.
   These are included in transaction metadata. The GAL logs them for
   audit purposes.

4. **Instant win tickets** — Hacksaw's scratch-card products have a single
   DEBIT (ticket purchase) and a single CREDIT (reveal result). The CREDIT
   may be 0 for losing tickets.

5. **In-flight rounds** — If a player disconnects mid-round, the round
   remains "in flight". Hacksaw will send the CREDIT when the round
   resolves server-side, even if the player has navigated away.
   Credits must be accepted offline (no active session required).

6. **Bonus buy** — Hacksaw slots support bonus buy. The debit amount is
   the bonus-buy price (much larger than a normal spin). The credit is
   the bonus result.

7. **HTTP signature** — Hacksaw signs callbacks with HMAC-SHA256 in the
   `X-Hacksaw-Signature` header: HMAC(request_body, operator_secret).

API reference: https://hacksaw.co/integration-docs/
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

SUPPLIER_ID = "hacksaw"

# Hacksaw transaction types
HS_BET = "BET"
HS_WIN = "WIN"           # Includes 0-amount close-round credits
HS_REFUND = "REFUND"
HS_BONUS_BUY = "BONUS_BUY"
HS_FREE_BET = "FREE_BET"


class HacksawProvider:
    """
    AccountsProvider for Hacksaw Gaming crash games and instant-win titles.

    Handles the provably-fair crash game flow where rounds are multiplayer
    and credits can arrive offline (after player session ends).
    """

    def __init__(
        self,
        operator_id: str,
        secret_key: str,
        currency: str = "EUR",
        http_timeout_s: float = 5.0,
    ) -> None:
        self._operator_id = operator_id
        self._secret_key = secret_key
        self._default_currency = currency
        self._http_client = httpx.AsyncClient(timeout=http_timeout_s)

    def verify_signature(self, request_body: bytes, received_sig: str) -> bool:
        """
        Verify Hacksaw's HMAC-SHA256 signature.

        Header: X-Hacksaw-Signature: sha256=<hex_digest>
        The signature covers the raw request body bytes.
        """
        expected = "sha256=" + _hmac.new(
            self._secret_key.encode(),
            request_body,
            hashlib.sha256,
        ).hexdigest()
        return _hmac.compare_digest(received_sig, expected)

    async def authenticate(self, token: str) -> PlayerSession:
        """Validate a Hacksaw session token."""
        logger.debug("Hacksaw authenticate token=%s...", token[:16])
        from transaction_result import AuthenticationError
        raise AuthenticationError("Not implemented: validate Hacksaw session token")

    async def get_balance(
        self, session: PlayerSession, game_id: Optional[str] = None
    ) -> BalanceStatus:
        """
        Return balance. Hacksaw expects amounts in minor units (cents).

        NOTE: Unlike Evolution (major units), Hacksaw uses minor units.
        Confirm with the supplier's integration spec for each currency.
        """
        logger.debug("Hacksaw get_balance player=%s", session.player_id)
        return BalanceStatus(
            cash_balance=Decimal("0"),
            bonus_balance=Decimal("0"),
            currency=session.currency or self._default_currency,
        )

    async def debit(
        self,
        session: PlayerSession,
        operation: DebitOperation,
        context: TransactionContext,
    ) -> TransactionResult:
        """
        Handle Hacksaw BET callback.

        For crash games, this is called when the player locks in their
        stake before the round starts. For bonus buy, the amount is the
        purchase price.
        """
        logger.info(
            "Hacksaw BET player=%s round=%s amount=%s",
            session.player_id, operation.round_id, operation.amount,
        )
        return await self.apply_transaction(session, [operation], context)

    async def credit(
        self,
        session: PlayerSession,
        operation: CreditOperation,
        context: TransactionContext,
    ) -> TransactionResult:
        """
        Handle Hacksaw WIN callback.

        IMPORTANT: Hacksaw sends WIN even for losing rounds (amount=0).
        A 0-amount WIN is a valid terminal event — do not reject it.
        Process it as a successful 0-credit to close the round.
        """
        if operation.amount == Decimal("0"):
            logger.debug(
                "Hacksaw WIN(zero) player=%s round=%s — closing round",
                session.player_id, operation.round_id,
            )
        else:
            logger.info(
                "Hacksaw WIN player=%s round=%s amount=%s",
                session.player_id, operation.round_id, operation.amount,
            )
        return await self.apply_transaction(session, [operation], context)

    async def refund(
        self,
        session: PlayerSession,
        operation: RefundOperation,
        context: TransactionContext,
    ) -> TransactionResult:
        """Handle Hacksaw REFUND (bet rollback for aborted crash round)."""
        logger.info(
            "Hacksaw REFUND player=%s round=%s",
            session.player_id, operation.round_id,
        )
        return await self.reverse_transaction(session, [operation], context)

    async def apply_transaction(
        self,
        session: PlayerSession,
        operations: list[SupplierOperation],
        context: TransactionContext,
    ) -> TransactionResult:
        """Apply Hacksaw wallet operations."""
        total_debit = sum((op.amount for op in operations if isinstance(op, DebitOperation)), Decimal("0"))
        total_credit = sum((op.amount for op in operations if isinstance(op, CreditOperation)), Decimal("0"))

        balance = BalanceStatus(
            cash_balance=Decimal("0"),
            bonus_balance=Decimal("0"),
            currency=session.currency or self._default_currency,
        )
        # Crash: a zero credit closes the round — classify as CREDIT
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
        """Reverse a Hacksaw transaction."""
        balance = BalanceStatus(
            cash_balance=Decimal("0"),
            bonus_balance=Decimal("0"),
            currency=session.currency or self._default_currency,
        )
        return success_result(
            tx_type=TransactionType.REFUND,
            balance=balance,
            tx_id=context.tx_id,
            external_id=context.supplier_ref,
        )

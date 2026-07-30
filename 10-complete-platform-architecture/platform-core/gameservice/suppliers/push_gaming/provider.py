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
suppliers/push_gaming/provider.py
----------------------------------
Push Gaming — High-volatility slots seamless-wallet integration.

Integration model
-----------------
Push Gaming is known for high-volatility, narrative-driven slots (Fat
Banker, Jammin' Jars, Razor Shark). They use a seamless-wallet REST/JSON
integration.

Push Gaming is available both via direct integration and through Relax
Gaming aggregation. This provider covers the direct integration.

Quirks and gotchas
------------------
1. **Anticipation feature** — Some Push Gaming titles have an "anticipation"
   mechanic where the game pauses for dramatic effect. During this pause,
   no wallet calls are made — the round is still in flight. Don't treat
   slow callback arrival as a timeout during anticipation rounds.

2. **Max win cap** — Push Gaming enforces a global max win (typically
   50,000x the bet). When a win exceeds this cap, Push sends a partial
   WIN and a separate VOID for the excess. The GAL must handle both.

3. **Buy feature** — Push titles offer a feature buy at typically 50–100x
   the base bet. The DEBIT amount is the buy price. Feature results are
   credited as a standard WIN.

4. **Free spins persistence** — Free spin awards have a 30-day expiry.
   If the player hasn't used their free spins, Push calls the GAL to
   forfeit them. This arrives as a REFUND of the free-spin award (not
   a player-initiated refund).

5. **Jackpot tiers** — Some Push games (e.g. Midnight Eclipse) have
   networked jackpots. Jackpot wins arrive as a separate WIN callback
   with `winType: JACKPOT` and are credited as cash (not bonus).

6. **Currency precision** — Push Gaming uses 2 decimal places for all
   currencies including JPY (which conventionally has 0 decimal places).
   Apply currency normalization after receiving amounts.

7. **Session validation** — Push validates the session on every callback
   (not just authentication). A session that has been logged out returns
   HTTP 401, which Push will retry. Ensure logged-out sessions return a
   clear INVALID_SESSION response.

API reference: https://developers.pushgaming.com/docs/
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

SUPPLIER_ID = "push_gaming"

# Push Gaming win types
WIN_TYPE_REGULAR = "REGULAR"
WIN_TYPE_FREE_SPIN = "FREE_SPIN"
WIN_TYPE_JACKPOT = "JACKPOT"
WIN_TYPE_BONUS_BUY = "BONUS_BUY"

# Push Gaming max win cap multiplier (platform configurable)
DEFAULT_MAX_WIN_MULTIPLIER = Decimal("50000")


class PushGamingProvider:
    """
    AccountsProvider for Push Gaming high-volatility slots.

    Includes special handling for jackpot wins, max-win enforcement,
    and offline free-spin credits.
    """

    def __init__(
        self,
        operator_key: str,
        secret: str,
        http_timeout_s: float = 5.0,
        max_win_multiplier: Decimal = DEFAULT_MAX_WIN_MULTIPLIER,
    ) -> None:
        self._operator_key = operator_key
        self._secret = secret
        self._http_client = httpx.AsyncClient(timeout=http_timeout_s)
        self._max_win_multiplier = max_win_multiplier

    def verify_signature(self, body: bytes, timestamp: str, received_sig: str) -> bool:
        """
        Verify Push Gaming's request signature.

        Push signs requests as: HMAC-SHA256(timestamp + "." + body, secret)
        Signature is in the X-Push-Signature header.
        """
        message = f"{timestamp}.".encode() + body
        expected = _hmac.new(self._secret.encode(), message, hashlib.sha256).hexdigest()
        return _hmac.compare_digest(received_sig, expected)

    def check_max_win(self, bet_amount: Decimal, win_amount: Decimal) -> Decimal:
        """
        Apply the max win cap. Returns the capped win amount.

        If the win exceeds 50,000x the bet, it is capped. The excess
        is void and a separate VOID callback handles the overage.
        """
        max_win = bet_amount * self._max_win_multiplier
        if win_amount > max_win:
            logger.warning(
                "Max win cap applied: win=%s cap=%s", win_amount, max_win
            )
            return max_win
        return win_amount

    async def authenticate(self, token: str) -> PlayerSession:
        """Validate a Push Gaming session token."""
        logger.debug("Push Gaming authenticate token=%s...", token[:16])
        from transaction_result import AuthenticationError
        raise AuthenticationError("Not implemented: validate Push Gaming session token")

    async def get_balance(
        self, session: PlayerSession, game_id: Optional[str] = None
    ) -> BalanceStatus:
        """Return balance for Push Gaming's balance request."""
        logger.debug("Push Gaming get_balance player=%s", session.player_id)
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
        """
        Handle Push Gaming DEBIT (bet or feature buy).

        Feature buys are large — up to 100x base bet. Ensure loss limits
        are calculated against the actual debit amount, not the base bet.
        """
        logger.info(
            "Push Gaming DEBIT player=%s round=%s amount=%s",
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
        Handle Push Gaming WIN credit.

        Jackpot wins are processed identically to regular wins but logged
        with a JACKPOT win_type for BI reporting.
        """
        logger.info(
            "Push Gaming CREDIT player=%s round=%s amount=%s",
            session.player_id, operation.round_id, operation.amount,
        )
        return await self.apply_transaction(session, [operation], context)

    async def refund(
        self,
        session: PlayerSession,
        operation: RefundOperation,
        context: TransactionContext,
    ) -> TransactionResult:
        """Handle Push Gaming REFUND (voided or expired free spins)."""
        logger.info(
            "Push Gaming REFUND player=%s round=%s",
            session.player_id, operation.round_id,
        )
        return await self.reverse_transaction(session, [operation], context)

    async def apply_transaction(
        self,
        session: PlayerSession,
        operations: list[SupplierOperation],
        context: TransactionContext,
    ) -> TransactionResult:
        """Apply Push Gaming wallet operations."""
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
        """Reverse a Push Gaming transaction."""
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

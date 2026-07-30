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
suppliers/nyx/provider.py
--------------------------
NYX Interactive / SG Digital — Aggregated game feed integration.

Integration model
-----------------
NYX Interactive (now SG Digital, part of Scientific Games) is an aggregator
that distributes content from many studios including NYX's own titles,
NextGen, Williams Interactive (WMS), Bally, and others. Their integration
uses the "Open Gaming System" (OGS) — a standardised API for seamless
wallet operations.

NYX/OGS was one of the early industry-standard wallet APIs and influenced
later specifications.

Quirks and gotchas
------------------
1. **OGS standard** — NYX's Open Gaming System defines a common wallet
   protocol adopted by many studios beyond just NYX titles. If you see
   OGS callbacks, they may originate from various studios within the feed.

2. **Game channel codes** — NYX/OGS uses channel codes to distinguish
   game sub-types: CASINO (slots), LIVE (live dealer), VIRTUAL (virtual
   sports), POKER. Route to different treatment based on channel.

3. **Country-based blocking** — NYX has strict country availability per
   game title. The `countryCode` in the callback reflects the player's
   registered country, not their current IP. Some titles are blocked for
   UK players (due to UKGC restrictions on certain math profiles).

4. **SMUX** — NYX's sub-multiplexing (SMUX) system allows multiple game
   sessions to share a single wallet connection. The `smuxId` in callbacks
   identifies the active sub-session. Track SMUX IDs for proper session
   management.

5. **Jackpot service** — NYX operates a networked jackpot service. Jackpot
   contributions are taken as a small % of each bet. Jackpot wins arrive
   as a separate credit with `txType: JACKPOT`. Always process jackpot
   credits offline (player may not be in session).

6. **Legacy XML** — Older NYX integrations use SOAP/XML. The OGS REST
   interface was introduced later. Some operator contracts still require
   XML support.

7. **Tournament credits** — NYX's tournament system sends credit callbacks
   with `txType: TOURNAMENT_WIN`. These should be processed identically
   to regular wins.

API reference: https://open-gaming.nyx.com/api/ (access-restricted)
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

SUPPLIER_ID = "nyx"

# OGS transaction types
OGS_DEBIT = "DEBIT"
OGS_CREDIT = "CREDIT"
OGS_REFUND = "REFUND"
OGS_JACKPOT = "JACKPOT"
OGS_TOURNAMENT_WIN = "TOURNAMENT_WIN"
OGS_FREE_ROUND = "FREE_ROUND"

# OGS channel codes
CHANNEL_CASINO = "CASINO"
CHANNEL_LIVE = "LIVE"
CHANNEL_VIRTUAL = "VIRTUAL"
CHANNEL_POKER = "POKER"

# OGS status codes
OGS_OK = "0"
OGS_INVALID_TOKEN = "1"
OGS_INSUFFICIENT_FUNDS = "2"
OGS_DUPLICATE_TRANSACTION = "3"
OGS_INTERNAL_ERROR = "99"


class NYXProvider:
    """
    AccountsProvider for NYX Interactive / SG Digital (OGS protocol).

    Implements the Open Gaming System (OGS) wallet service spec.
    Handles all game types in the NYX feed including jackpots and
    tournament rewards.
    """

    def __init__(
        self,
        operator_id: str,
        auth_token: str,
        http_timeout_s: float = 5.0,
    ) -> None:
        self._operator_id = operator_id
        self._auth_token = auth_token
        self._http_client = httpx.AsyncClient(
            timeout=http_timeout_s,
            headers={"Authorization": f"Bearer {auth_token}"},
        )

    async def authenticate(self, token: str) -> PlayerSession:
        """
        Validate an OGS session token.

        OGS tokens are issued by the operator and carry player context.
        NYX calls this on every callback (no separate auth step).
        """
        logger.debug("NYX OGS authenticate token=%s...", token[:16])
        from transaction_result import AuthenticationError
        raise AuthenticationError("Not implemented: validate NYX OGS session token")

    async def get_balance(
        self, session: PlayerSession, game_id: Optional[str] = None
    ) -> BalanceStatus:
        """
        Return balance for OGS balance requests.

        OGS requests may specify a `smuxId` for sub-session tracking.
        All sub-sessions share the same underlying wallet.
        """
        logger.debug("NYX OGS get_balance player=%s", session.player_id)
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
        """Handle OGS DEBIT callback."""
        logger.info(
            "NYX OGS DEBIT player=%s round=%s amount=%s",
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
        Handle OGS CREDIT / JACKPOT / TOURNAMENT_WIN callback.

        Jackpot and tournament wins are processed identically to regular
        credits but logged with their specific type for reporting.
        """
        logger.info(
            "NYX OGS CREDIT player=%s round=%s amount=%s",
            session.player_id, operation.round_id, operation.amount,
        )
        return await self.apply_transaction(session, [operation], context)

    async def refund(
        self,
        session: PlayerSession,
        operation: RefundOperation,
        context: TransactionContext,
    ) -> TransactionResult:
        """Handle OGS REFUND callback."""
        logger.info(
            "NYX OGS REFUND player=%s round=%s",
            session.player_id, operation.round_id,
        )
        return await self.reverse_transaction(session, [operation], context)

    async def apply_transaction(
        self,
        session: PlayerSession,
        operations: list[SupplierOperation],
        context: TransactionContext,
    ) -> TransactionResult:
        """Apply NYX OGS wallet operations."""
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
        """Reverse a NYX OGS transaction."""
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

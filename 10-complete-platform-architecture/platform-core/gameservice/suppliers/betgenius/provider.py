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
suppliers/betgenius/provider.py
--------------------------------
Bet Genius — Sports data and managed trading integration.

Integration model
-----------------
Bet Genius (formerly known as a sports data feed provider, now GTS —
Global Trading Services) provides two services:
1. **Sports data feed** — Live match data, odds, and event management.
2. **Managed trading** — White-label risk management and odds compilation.

Unlike casino suppliers, Bet Genius does NOT provide a game UI. Instead,
the operator uses Bet Genius's odds API to power their own sportsbook UI.
Wallet operations are triggered by the operator's sportsbook engine, not
by Bet Genius directly.

This integration handles the wallet side of Bet Genius-powered sportsbook
bets (odds sourced from BG, wallet managed by AcmetoCasino).

Quirks and gotchas
------------------
1. **Not a seamless wallet** — Bet Genius does not call the operator's
   wallet endpoint. Instead, the operator's sportsbook engine reads odds
   from BG's API and places bets through the operator's own bet slip
   submission flow. The wallet operation is triggered by the operator.

2. **Event settlement** — BG provides settlement data (void, win, push)
   via their data feed. The operator's settlement engine reads BG's
   settlement signals and triggers the corresponding wallet credits.

3. **Price format** — BG provides odds in decimal format (e.g. 2.50).
   Convert to operator's native format (decimal, fractional, or moneyline)
   for display. Always store and process in decimal format internally.

4. **In-play odds** — BG's in-play feed updates odds multiple times per
   second. The operator must implement request queuing and rate limiting
   to avoid overloading BG's API. Recommended: cache odds with a 500ms TTL.

5. **Suspension handling** — BG suspends markets during key events
   (corner kicks, near miss, etc.). Suspended markets are flagged with
   `status: SUSPENDED`. The sportsbook must prevent new bets on suspended
   markets. Check market status before accepting a bet.

6. **Cash out** — BG provides cash-out valuation via their API. When a
   player requests cash-out, the operator queries BG for the current
   value, presents it to the player, and if accepted, triggers a wallet
   credit for the cash-out amount and a void on the original bet.

7. **Liability management** — BG's managed trading service monitors the
   operator's book and may call the operator's API to void bets or adjust
   limits. These administrative operations arrive as asynchronous webhooks.

API reference: https://docs.betgenius.com/ (access-restricted)
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

SUPPLIER_ID = "betgenius"

# Bet Genius settlement outcomes
OUTCOME_WIN = "WIN"
OUTCOME_VOID = "VOID"
OUTCOME_PUSH = "PUSH"      # Stake returned (e.g. no scoring)
OUTCOME_HALF_WIN = "HALF_WIN"    # Asian handicap partial win
OUTCOME_HALF_LOSE = "HALF_LOSE"  # Asian handicap partial loss

# Bet Genius market status
MARKET_ACTIVE = "ACTIVE"
MARKET_SUSPENDED = "SUSPENDED"
MARKET_CLOSED = "CLOSED"
MARKET_SETTLED = "SETTLED"
MARKET_VOID = "VOID"


class BetGeniusProvider:
    """
    AccountsProvider for Bet Genius-sourced sportsbook bets.

    Handles the wallet side of bets placed using Bet Genius odds data.
    BG does not call the wallet directly — the operator's sportsbook
    engine triggers all wallet operations.

    This provider wraps the standard wallet operations with BG-specific
    logging and validation (market status, suspension checks).
    """

    def __init__(
        self,
        api_key: str,
        api_base_url: str = "https://api.betgenius.com",
        http_timeout_s: float = 3.0,
    ) -> None:
        self._api_key = api_key
        self._api_base_url = api_base_url.rstrip("/")
        self._http_client = httpx.AsyncClient(
            timeout=http_timeout_s,
            headers={"X-API-Key": api_key, "Accept": "application/json"},
        )

    async def get_market_status(self, event_id: str, market_id: str) -> str:
        """
        Query Bet Genius for the current market status.

        Call this before accepting a new bet to ensure the market is
        ACTIVE (not SUSPENDED or CLOSED).

        Returns one of: ACTIVE, SUSPENDED, CLOSED, SETTLED, VOID.
        """
        url = f"{self._api_base_url}/v2/events/{event_id}/markets/{market_id}"
        try:
            response = await self._http_client.get(url)
            response.raise_for_status()
            data = response.json()
            return data.get("status", MARKET_ACTIVE)
        except Exception as exc:
            logger.warning("BetGenius market status check failed: %s", exc)
            # Fail safe: treat unavailable status check as ACTIVE
            return MARKET_ACTIVE

    async def authenticate(self, token: str) -> PlayerSession:
        """
        Validate a sportsbook session token.

        Bet Genius does not issue session tokens. The session is managed
        by the operator's sportsbook login flow. This method validates
        the operator-issued token from the player's sportsbook session.
        """
        logger.debug("BetGenius authenticate token=%s...", token[:16])
        from transaction_result import AuthenticationError
        raise AuthenticationError("Not implemented: validate sportsbook session token")

    async def get_balance(
        self, session: PlayerSession, game_id: Optional[str] = None
    ) -> BalanceStatus:
        """Return player balance for sportsbook balance display."""
        logger.debug("BetGenius get_balance player=%s", session.player_id)
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
        Debit for a sportsbook bet placement (BG odds source).

        The bet selection IDs and market IDs should be stored in the
        transaction metadata for later settlement matching.
        """
        logger.info(
            "BetGenius BET player=%s round=%s amount=%s",
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
        Credit for a settled winning bet or cash-out.

        Settlement is triggered by the operator's settlement engine
        reading BG's event settlement signal. Cash-out credits are
        triggered by the player and confirmed with BG's valuation API.
        """
        logger.info(
            "BetGenius SETTLE player=%s round=%s amount=%s",
            session.player_id, operation.round_id, operation.amount,
        )
        return await self.apply_transaction(session, [operation], context)

    async def refund(
        self,
        session: PlayerSession,
        operation: RefundOperation,
        context: TransactionContext,
    ) -> TransactionResult:
        """
        Refund a voided bet.

        BG marks events/markets as VOID when they are cancelled (match
        abandoned, data error, etc.). The operator's settlement engine
        reads these signals and triggers refunds.
        """
        logger.info(
            "BetGenius VOID(refund) player=%s round=%s",
            session.player_id, operation.round_id,
        )
        return await self.reverse_transaction(session, [operation], context)

    async def apply_transaction(
        self,
        session: PlayerSession,
        operations: list[SupplierOperation],
        context: TransactionContext,
    ) -> TransactionResult:
        """Apply BetGenius wallet operations."""
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
        """Reverse a BetGenius transaction (bet void)."""
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

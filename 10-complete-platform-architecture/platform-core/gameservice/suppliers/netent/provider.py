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
suppliers/netent/provider.py
-----------------------------
NetEnt — Slots + Free Rounds seamless-wallet integration.

Integration model
-----------------
NetEnt (now part of Evolution after the 2020 acquisition) uses a
seamless-wallet model. The operator exposes a wallet service that NetEnt
calls for every transaction. NetEnt's wallet protocol is SOAP/XML-based
for legacy integrations and JSON for newer games.

Quirks and gotchas
------------------
1. **Dual protocol** — Older NetEnt titles use a SOAP XML wallet service.
   Newer titles (after ~2018) use a JSON REST interface. The operator must
   support both. This implementation uses the JSON interface.

2. **Token service** — NetEnt requires a separate token service endpoint
   (`/token/`) that the game client calls directly to exchange the launch
   token for a session token. The token service validates the launch token
   and issues a short-lived session token.

3. **Plugin servlet** — Some NetEnt integrations use a "plugin" model where
   NetEnt calls a server-side endpoint to render game configuration. This
   is legacy and being phased out.

4. **Free rounds** — NetEnt's free-round flow is more complex than most
   suppliers. Free-round bets have amount=0 (the supplier funds the stake)
   but the win callback carries the actual award amount. The platform must
   distinguish between real-money and free-round callbacks.

5. **Balance in cents** — Unlike Evolution (major units), NetEnt expects
   balance in cents (minor units). Be careful when switching between the
   two in multi-supplier environments.

6. **Transaction de-duplication** — NetEnt identifies duplicate transactions
   by `transactionId`. If a duplicate is received, the operator must return
   HTTP 200 with the original result (not an error).

7. **Jackpot contributions** — NetEnt deducts a small amount from each bet
   for jackpot contribution. This appears as a separate JACKPOT_DEBIT
   operation code. Handle it as a standard debit.

API reference: https://casinomodule.com/docs/netent-wallet-api/
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

SUPPLIER_ID = "netent"

# NetEnt operation codes
OP_BET = "BET"
OP_WIN = "WIN"
OP_REFUND = "REFUND"
OP_JACKPOT_WIN = "JACKPOT_WIN"
OP_FREE_ROUND_BET = "FREEROUND_BET"
OP_FREE_ROUND_WIN = "FREEROUND_WIN"
OP_JACKPOT_DEBIT = "JACKPOT_DEBIT"


class NetEntProvider:
    """
    AccountsProvider for NetEnt slots and free-round products.

    Supports both real-money and free-round game sessions. Free-round
    bets are tracked separately to ensure correct bonus accounting.
    """

    def __init__(
        self,
        wallet_url: str,
        token_service_url: str,
        operator_id: str,
        secret: str,
        http_timeout_s: float = 5.0,
    ) -> None:
        self._wallet_url = wallet_url.rstrip("/")
        self._token_service_url = token_service_url.rstrip("/")
        self._operator_id = operator_id
        self._secret = secret
        self._http_client = httpx.AsyncClient(timeout=http_timeout_s)

    async def authenticate(self, token: str) -> PlayerSession:
        """
        Validate a NetEnt game token via the token service.

        NetEnt's token flow:
        1. Operator generates a launch token during game init.
        2. Game client calls the operator's /token/ endpoint with the token.
        3. Operator validates and returns session details.
        4. NetEnt includes the validated token in all subsequent callbacks.
        """
        logger.debug("NetEnt authenticate token=%s...", token[:16])
        # In production: validate token against session store
        # Return PlayerSession if valid, raise AuthenticationError if not
        from transaction_result import AuthenticationError
        raise AuthenticationError("Not implemented: validate token against session store")

    def validate_request_signature(self, token: str, timestamp: str, received_sig: str) -> bool:
        """
        Validate the HMAC-SHA256 signature on inbound NetEnt requests.

        NetEnt signs requests as: HMAC-SHA256(operator_id + token + timestamp, secret)
        """
        expected = _hmac.new(
            self._secret.encode(),
            f"{self._operator_id}{token}{timestamp}".encode(),
            hashlib.sha256,
        ).hexdigest()
        return _hmac.compare_digest(received_sig, expected)

    async def get_balance(
        self, session: PlayerSession, game_id: Optional[str] = None
    ) -> BalanceStatus:
        """Return balance in minor units (cents/pence) — NetEnt's format."""
        logger.debug("NetEnt get_balance player=%s", session.player_id)
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
        """Handle a NetEnt BET or FREEROUND_BET callback."""
        is_free_round = getattr(operation, "is_free_round", False)
        op_code = OP_FREE_ROUND_BET if is_free_round else OP_BET
        logger.info(
            "NetEnt %s player=%s round=%s amount=%s",
            op_code, session.player_id, operation.round_id, operation.amount,
        )
        return await self.apply_transaction(session, [operation], context)

    async def credit(
        self,
        session: PlayerSession,
        operation: CreditOperation,
        context: TransactionContext,
    ) -> TransactionResult:
        """Handle a NetEnt WIN, FREEROUND_WIN, or JACKPOT_WIN callback."""
        logger.info(
            "NetEnt WIN player=%s round=%s amount=%s",
            session.player_id, operation.round_id, operation.amount,
        )
        return await self.apply_transaction(session, [operation], context)

    async def refund(
        self,
        session: PlayerSession,
        operation: RefundOperation,
        context: TransactionContext,
    ) -> TransactionResult:
        """Handle a NetEnt REFUND callback."""
        logger.info(
            "NetEnt REFUND player=%s round=%s",
            session.player_id, operation.round_id,
        )
        return await self.reverse_transaction(session, [operation], context)

    async def apply_transaction(
        self,
        session: PlayerSession,
        operations: list[SupplierOperation],
        context: TransactionContext,
    ) -> TransactionResult:
        """Apply NetEnt wallet operations."""
        total_debit = sum(
            (op.amount for op in operations if isinstance(op, DebitOperation)), Decimal("0")
        )
        total_credit = sum(
            (op.amount for op in operations if isinstance(op, CreditOperation)), Decimal("0")
        )

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
        """Reverse a NetEnt transaction."""
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

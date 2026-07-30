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
suppliers/evolution/provider.py
--------------------------------
Evolution Gaming — Live dealer seamless-wallet integration.

Integration model
-----------------
Evolution uses a **seamless wallet** (also called "transfer wallet").
The game client launches via a signed URL. During play, Evolution's
infrastructure calls back into the operator's GAL for every wallet
event. The GAL never initiates calls to Evolution's API for wallet
operations — all traffic is inbound.

Quirks and gotchas
------------------
1. **Single callback per round** — Evolution sends a single JSON POST
   that may contain BOTH a debit (WITHDRAW) and a credit (DEPOSIT) in
   the same request. The `transaction.type` field can be:
   - WITHDRAW — debit only (bet placed)
   - DEPOSIT  — credit only (win paid)
   - WITHDRAW_AND_DEPOSIT — atomic debit + credit (e.g. blackjack)

2. **Idempotency via `transaction.id`** — Evolution re-sends callbacks
   on network failures. The GAL must return the same result for duplicate
   `transaction.id` values.

3. **uuid field** — Every Evolution request includes a `uuid` (their
   correlation ID). This must be echoed back in the response for Evolution
   to match the response to the request.

4. **Promotional transactions** — Evolution sends separate promo callbacks
   (jackpot wins, free-round completions) with a `promoTransaction` object.
   These are processed as credits with a special action code.

5. **Reality check** — Evolution supports the generic iframe RC flow.
   When the platform's RC timer elapses, the next balance/transaction
   response sets `retrasmission: true` to trigger Evolution's RC overlay.

6. **Balance format** — Evolution expects balances in major units (e.g.
   GBP 10.50 not 1050p). The provider converts before responding.

7. **Status codes** — Evolution has its own set of string status codes
   (OK, INSUFFICIENT_FUNDS, INVALID_TOKEN_ID, etc.). The provider maps
   platform exceptions to these codes.

API reference: https://evolution.gitbook.io/seamless-wallet-api/
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import uuid as _uuid
from decimal import Decimal
from typing import Optional

import httpx
from pydantic import BaseModel

from accounts_provider import (
    AccountsProvider,
    AdjustOperation,
    ClawbackOperation,
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

SUPPLIER_ID = "evolution"

# ---------------------------------------------------------------------------
# Evolution protocol models
# ---------------------------------------------------------------------------


class EvoTransaction(BaseModel):
    id: str
    refId: str
    amount: Decimal


class EvoRequest(BaseModel):
    authToken: str
    sid: Optional[str] = None
    playerId: str
    uuid: str
    currency: Optional[str] = None
    transaction: Optional[EvoTransaction] = None


class EvoResponse(BaseModel):
    status: str
    balance: Optional[Decimal] = None
    bonus: Optional[Decimal] = None
    retrasmission: Optional[bool] = None  # Note: Evolution typo in spec
    uuid: str


# Evolution status codes — these are the strings Evolution's SDK expects
STATUS_OK = "OK"
STATUS_TEMPORARY_ERROR = "TEMPORARY_ERROR"
STATUS_INVALID_TOKEN = "INVALID_TOKEN_ID"
STATUS_INVALID_SID = "INVALID_SID"
STATUS_ACCOUNT_LOCKED = "ACCOUNT_LOCKED"
STATUS_INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
STATUS_BET_ALREADY_EXISTS = "BET_ALREADY_EXIST"
STATUS_BET_DOES_NOT_EXIST = "BET_DOES_NOT_EXIST"
STATUS_UNKNOWN_ERROR = "UNKNOWN_ERROR"
STATUS_CASINO_LIMIT_EXCEEDED = "CASINO_LIMIT_EXCEEDED_LOSS"


# ---------------------------------------------------------------------------
# Provider implementation
# ---------------------------------------------------------------------------


class EvolutionProvider:
    """
    AccountsProvider implementation for Evolution Gaming.

    Evolution operates as a seamless-wallet supplier. All wallet calls
    originate from Evolution's servers, not from the game client.

    The provider handles authentication via Evolution's operator-signed
    tokens and forwards wallet operations to the platform's AccountsBridge.
    """

    def __init__(
        self,
        api_base_url: str,
        api_secret: str,
        operator_id: str,
        http_timeout_s: float = 5.0,
    ) -> None:
        self._api_base_url = api_base_url.rstrip("/")
        self._api_secret = api_secret
        self._operator_id = operator_id
        self._http_timeout_s = http_timeout_s
        self._http_client = httpx.AsyncClient(timeout=http_timeout_s)

    # Launch tokens are valid for this long after issuance. A leaked token
    # (logged, cached, replayed from a stale client) stops working once
    # this window elapses, instead of remaining valid indefinitely until
    # the shared secret is rotated.
    TOKEN_TTL_SECONDS = 300

    async def authenticate(self, token: str) -> PlayerSession:
        """
        Validate an Evolution auth token.

        Evolution passes the operator-issued token back on every callback.
        The token was generated by the platform during game launch and
        signed with HMAC-SHA256 using the shared secret.

        The token format is:
        base64(player_id:brand_id:game_id:currency:country:jurisdiction:timestamp)
        signed as: token.HMAC(token, secret)

        timestamp is a Unix epoch seconds string set at issuance; tokens
        older than TOKEN_TTL_SECONDS are rejected even with a valid
        signature.
        """
        import base64
        import time

        try:
            # Split token from signature
            parts = token.rsplit(".", 1)
            if len(parts) != 2:
                raise ValueError("Invalid token format")

            payload_b64, signature = parts
            expected_sig = hmac.new(
                self._api_secret.encode(),
                payload_b64.encode(),
                hashlib.sha256,
            ).hexdigest()

            if not hmac.compare_digest(signature, expected_sig):
                from transaction_result import AuthenticationError
                raise AuthenticationError("Evolution token signature invalid")

            payload = base64.b64decode(payload_b64 + "==").decode()
            player_id, brand_id, game_id, currency, country, jurisdiction, issued_at = payload.split(":")

            token_age = time.time() - float(issued_at)
            if token_age > self.TOKEN_TTL_SECONDS or token_age < 0:
                from transaction_result import AuthenticationError
                raise AuthenticationError(
                    f"Evolution token expired (age={token_age:.0f}s, "
                    f"ttl={self.TOKEN_TTL_SECONDS}s)"
                )

            return PlayerSession(
                player_id=player_id,
                brand_id=brand_id,
                external_id=player_id,
                currency=currency,
                country=country,
                jurisdiction=jurisdiction,
                session_token=token,
                game_id=game_id,
                mobile=False,
            )
        except Exception as exc:
            from transaction_result import AuthenticationError
            logger.warning("Evolution authentication failed: %s", exc)
            raise AuthenticationError(f"Evolution token invalid: {exc}") from exc

    async def get_balance(
        self, session: PlayerSession, game_id: Optional[str] = None
    ) -> BalanceStatus:
        """
        Return the player's balance in major units (Evolution expects GBP not pence).

        Evolution calls this before displaying the game UI to ensure the
        player has a valid session and non-zero balance.
        """
        # In production, delegate to the platform wallet
        # Here we return a stub for illustration
        logger.debug("Evolution get_balance player=%s", session.player_id)
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
        """Process a WITHDRAW callback from Evolution."""
        logger.info(
            "Evolution WITHDRAW player=%s round=%s amount=%s",
            session.player_id, operation.round_id, operation.amount,
        )
        return await self.apply_transaction(session, [operation], context)

    async def credit(
        self,
        session: PlayerSession,
        operation: CreditOperation,
        context: TransactionContext,
    ) -> TransactionResult:
        """Process a DEPOSIT callback from Evolution."""
        logger.info(
            "Evolution DEPOSIT player=%s round=%s amount=%s",
            session.player_id, operation.round_id, operation.amount,
        )
        return await self.apply_transaction(session, [operation], context)

    async def refund(
        self,
        session: PlayerSession,
        operation: RefundOperation,
        context: TransactionContext,
    ) -> TransactionResult:
        """Reverse an incomplete round (Evolution calls this CANCEL)."""
        logger.info(
            "Evolution CANCEL player=%s round=%s",
            session.player_id, operation.round_id,
        )
        return await self.reverse_transaction(session, [operation], context)

    async def apply_transaction(
        self,
        session: PlayerSession,
        operations: list[SupplierOperation],
        context: TransactionContext,
    ) -> TransactionResult:
        """
        Apply a composite Evolution transaction.

        Evolution's WITHDRAW_AND_DEPOSIT transaction type requires both a
        debit and a credit to be applied atomically. This method iterates
        over the operations and delegates to the wallet layer.

        The wallet applies operations in order: debit first, then credit.
        If the debit fails (insufficient funds), the credit is not applied.
        """
        balance = BalanceStatus(
            cash_balance=Decimal("0"),
            bonus_balance=Decimal("0"),
            currency=session.currency or "GBP",
        )

        total_cash_usage = Decimal("0")
        total_bonus_usage = Decimal("0")
        last_tx_id = context.tx_id

        for op in operations:
            if isinstance(op, DebitOperation):
                # Wallet debit — raises InsufficientFundsError if balance too low
                logger.debug(
                    "Evolution applying debit: player=%s amount=%s",
                    session.player_id, op.amount,
                )
                # In production: call wallet.debit() here
                total_cash_usage += op.amount

            elif isinstance(op, CreditOperation):
                logger.debug(
                    "Evolution applying credit: player=%s amount=%s",
                    session.player_id, op.amount,
                )
                # In production: call wallet.credit() here

            elif isinstance(op, (AdjustOperation, ClawbackOperation)):
                logger.debug(
                    "Evolution applying adjust/clawback: player=%s",
                    session.player_id,
                )

        return success_result(
            tx_type=TransactionType.DEBIT,
            balance=balance,
            tx_id=last_tx_id,
            external_id=context.supplier_ref,
            cash_usage=total_cash_usage,
            bonus_usage=total_bonus_usage,
        )

    async def reverse_transaction(
        self,
        session: PlayerSession,
        operations: list[SupplierOperation],
        context: TransactionContext,
    ) -> TransactionResult:
        """Reverse a composite Evolution transaction."""
        logger.info(
            "Evolution reverse_transaction player=%s ref=%s",
            session.player_id, context.supplier_ref,
        )
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

    def _sign_request(self, payload: str) -> str:
        """Sign an outbound request payload with HMAC-SHA256."""
        return hmac.new(
            self._api_secret.encode(),
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()

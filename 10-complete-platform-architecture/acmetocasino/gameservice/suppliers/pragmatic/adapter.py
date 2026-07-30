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
gameservice.suppliers.pragmatic.adapter — Pragmatic Play Adapter
=================================================================

Implements the SEAMLESS wallet integration for Pragmatic Play (slots and live
casino).

SEAMLESS flow
-------------
In the SEAMLESS model Pragmatic calls the platform's wallet endpoints
for every round event.  The API layer routes these inbound calls to this
adapter's ``handle_*`` methods:

1. Pragmatic calls ``/balance`` → :meth:`handle_balance`
2. Player places a bet → Pragmatic calls ``/debit`` → :meth:`handle_debit`
3. Round settles  → Pragmatic calls ``/credit`` → :meth:`handle_credit`
4. Round abandoned → Pragmatic calls ``/rollback`` → :meth:`handle_rollback`
5. Promo win       → Pragmatic calls ``/promoWin`` → :meth:`handle_promo_win`

Each inbound callback carries an MD5 hash that is verified before processing.

Drops & Wins
------------
Pragmatic's Drops & Wins tournament engine can credit prizes directly to the
player's wallet via the ``/promoWin`` endpoint.  These are treated as bonus
credits on the platform side.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from acmetocasino.gameservice.errors import GameServiceError, InsufficientFundsError
from acmetocasino.gameservice.models.enums import ActionCode, CommandType, GameMode
from acmetocasino.gameservice.models.launch_request import LaunchRequest
from acmetocasino.gameservice.models.round_command import RoundCommand
from acmetocasino.gameservice.models.wallet_snapshot import WalletSnapshot
from acmetocasino.gameservice.suppliers.base import (
    BaseSupplierAdapter,
    LaunchResult,
    TransactionResult,
)
from acmetocasino.gameservice.suppliers.pragmatic.config import PragmaticConfig
from acmetocasino.gameservice.suppliers.pragmatic.models import (
    PragmaticCallbackResponse,
    PragmaticCreditRequest,
    PragmaticDebitRequest,
    PragmaticRollbackRequest,
)
from acmetocasino.gameservice.suppliers.pragmatic.translator import (
    build_callback_response,
    build_launch_url,
    map_action_code,
    verify_pragmatic_hash,
)


class PragmaticAdapter(BaseSupplierAdapter):
    """Adapter for Pragmatic Play's SEAMLESS wallet integration.

    Handles both outbound session launch and inbound wallet callback routing.

    Parameters
    ----------
    config:
        :class:`~acmetocasino.gameservice.suppliers.pragmatic.config.PragmaticConfig`
        with operator credentials and endpoint configuration.
    """

    supplier_id = "pragmatic"

    def __init__(self, config: PragmaticConfig | Any) -> None:
        super().__init__(config)
        if isinstance(config, PragmaticConfig):
            self._pp_config: PragmaticConfig = config
        else:
            self._pp_config = PragmaticConfig.model_validate(config.model_dump())

    # ------------------------------------------------------------------
    # SupplierAdapter: outbound session launch
    # ------------------------------------------------------------------

    def _do_launch(
        self, request: LaunchRequest, correlation_id: str
    ) -> LaunchResult:
        """Build the Pragmatic Play game launch URL."""
        session_id = str(uuid.uuid4())
        game_url = build_launch_url(
            request=request,
            secure_login=self._pp_config.secure_login,
            api_base_url=self._pp_config.api_base_url,
        )

        from datetime import timedelta
        return LaunchResult(
            session_id=session_id,
            game_url=game_url,
            token=request.player.session_token,
            expires_at=self._utcnow() + timedelta(hours=4),
            metadata={
                "game_id": request.game_id,
                "mode": request.mode.value,
                "casino_id": self._pp_config.casino_id,
            },
        )

    def _do_get_balance(self, session_id: str) -> WalletSnapshot:
        """Stub balance — in production: query wallet service by session."""
        return WalletSnapshot(
            cash_balance=Decimal("0"),
            currency="EUR",
            snapshot_at=self._utcnow().isoformat(),
        )

    def _do_debit(
        self,
        session_id: str,
        round_id: str,
        amount: Decimal,
        command: RoundCommand,
    ) -> TransactionResult:
        """Apply a wager debit to the player's wallet."""
        balance = self._do_get_balance(session_id)
        if balance.cash_balance + balance.bonus_balance < amount:
            raise InsufficientFundsError(
                message="Insufficient funds for Pragmatic debit",
                requested_amount=str(amount),
                available_balance=str(balance.total_balance),
            )
        # Deduct from cash first, then bonus
        cash_use = min(amount, balance.cash_balance)
        bonus_use = amount - cash_use
        updated = balance.with_cash_delta(-cash_use).with_bonus_delta(-bonus_use)
        return TransactionResult(
            transaction_id=self._new_transaction_id(),
            supplier_ref=command.supplier_ref,
            balance_after=updated,
        )

    def _do_credit(
        self,
        session_id: str,
        round_id: str,
        amount: Decimal,
        command: RoundCommand,
    ) -> TransactionResult:
        """Apply a win credit to the player's wallet."""
        balance = self._do_get_balance(session_id)
        updated = balance.with_cash_delta(amount)
        return TransactionResult(
            transaction_id=self._new_transaction_id(),
            supplier_ref=command.supplier_ref,
            balance_after=updated,
        )

    def _do_rollback(
        self,
        session_id: str,
        round_id: str,
        original_ref: str,
    ) -> TransactionResult:
        """Reverse a previously applied debit."""
        balance = self._do_get_balance(session_id)
        return TransactionResult(
            transaction_id=self._new_transaction_id(),
            supplier_ref=original_ref,
            balance_after=balance,
        )

    def _do_end_session(self, session_id: str) -> None:
        """Pragmatic sessions expire passively; no explicit termination needed."""
        self._logger.debug(
            "pragmatic.end_session",
            extra={"session_id": session_id},
        )

    # ------------------------------------------------------------------
    # Inbound SEAMLESS callback handlers
    # ------------------------------------------------------------------

    def handle_balance(
        self,
        session_token: str,
        params: dict[str, str],
    ) -> PragmaticCallbackResponse:
        """Respond to Pragmatic's balance query.

        Parameters
        ----------
        session_token:
            Platform session token from the callback.
        params:
            All query parameters from the Pragmatic request (for hash verification).
        """
        self._verify_hash(params)
        balance = self._do_get_balance(session_token)
        return build_callback_response(
            transaction_id="",
            cash=balance.cash_balance,
            bonus=balance.bonus_balance,
            currency=balance.currency,
        )

    def handle_debit(
        self,
        req: PragmaticDebitRequest,
        params: dict[str, str],
    ) -> PragmaticCallbackResponse:
        """Handle an inbound Pragmatic bet callback."""
        self._verify_hash(params)
        action_code = map_action_code(req.actionId)
        command = RoundCommand(
            command_type=CommandType.DEBIT,
            round_id=req.roundId,
            amount=Decimal(req.amount),
            action_code=action_code,
            supplier_ref=req.transactionId,
        )
        try:
            result = self.debit(req.token, req.roundId, Decimal(req.amount), command)
        except InsufficientFundsError as exc:
            return PragmaticCallbackResponse(
                error=2,
                description="Insufficient funds",
                currency=req.currency,
                cash="0",
                transactionId="",
            )
        return build_callback_response(
            transaction_id=result.transaction_id,
            cash=result.balance_after.cash_balance,
            bonus=result.balance_after.bonus_balance,
            currency=req.currency,
        )

    def handle_credit(
        self,
        req: PragmaticCreditRequest,
        params: dict[str, str],
    ) -> PragmaticCallbackResponse:
        """Handle an inbound Pragmatic win callback."""
        self._verify_hash(params)
        action_code = map_action_code(req.actionId)
        command = RoundCommand(
            command_type=CommandType.CREDIT,
            round_id=req.roundId,
            amount=Decimal(req.amount),
            action_code=action_code,
            supplier_ref=req.transactionId,
        )
        result = self.credit(req.token, req.roundId, Decimal(req.amount), command)
        return build_callback_response(
            transaction_id=result.transaction_id,
            cash=result.balance_after.cash_balance,
            bonus=result.balance_after.bonus_balance,
            currency=req.currency,
        )

    def handle_rollback(
        self,
        req: PragmaticRollbackRequest,
        params: dict[str, str],
    ) -> PragmaticCallbackResponse:
        """Handle an inbound Pragmatic rollback callback."""
        self._verify_hash(params)
        result = self.rollback(req.token, req.roundId, req.transactionId)
        # For rollback the balance is returned from the last known state
        balance = self._do_get_balance(req.token)
        return build_callback_response(
            transaction_id=result.transaction_id,
            cash=balance.cash_balance,
            bonus=balance.bonus_balance,
            currency=req.currency,
        )

    def handle_promo_win(
        self,
        session_token: str,
        amount: Decimal,
        transaction_id: str,
        currency: str,
        params: dict[str, str],
    ) -> PragmaticCallbackResponse:
        """Handle a Drops & Wins promotional prize credit."""
        self._verify_hash(params)
        command = RoundCommand(
            command_type=CommandType.CREDIT,
            round_id=f"promo-{transaction_id}",
            amount=amount,
            action_code=ActionCode.REGULAR,
            supplier_ref=transaction_id,
        )
        result = self.credit(session_token, command.round_id, amount, command)
        return build_callback_response(
            transaction_id=result.transaction_id,
            cash=result.balance_after.cash_balance,
            bonus=result.balance_after.bonus_balance,
            currency=currency,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _verify_hash(self, params: dict[str, str]) -> None:
        """Verify the Pragmatic MD5 hash in the request params."""
        if not self._pp_config.secret_key:
            return  # Hash verification disabled when no secret configured
        received = params.get("hash", "")
        clean_params = {k: v for k, v in params.items() if k != "hash"}
        if not verify_pragmatic_hash(clean_params, received, self._pp_config.secret_key):
            raise GameServiceError(
                message="Pragmatic Play callback hash verification failed"
            )


__all__ = ["PragmaticAdapter"]

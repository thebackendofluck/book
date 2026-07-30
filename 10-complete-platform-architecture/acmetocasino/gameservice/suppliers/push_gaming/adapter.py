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
gameservice.suppliers.push_gaming.adapter — Push Gaming Adapter
================================================================

SEAMLESS wallet integration for Push Gaming slots.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

from acmetocasino.gameservice.errors import GameServiceError, InsufficientFundsError
from acmetocasino.gameservice.models.enums import CommandType
from acmetocasino.gameservice.models.launch_request import LaunchRequest
from acmetocasino.gameservice.models.round_command import RoundCommand
from acmetocasino.gameservice.models.wallet_snapshot import WalletSnapshot
from acmetocasino.gameservice.suppliers.base import (
    BaseSupplierAdapter,
    LaunchResult,
    TransactionResult,
)
from acmetocasino.gameservice.suppliers.push_gaming.config import PushGamingConfig
from acmetocasino.gameservice.suppliers.push_gaming.models import (
    PushGamingCreditRequest,
    PushGamingDebitRequest,
    PushGamingRollbackRequest,
    PushGamingWalletResponse,
)
from acmetocasino.gameservice.suppliers.push_gaming.translator import (
    build_push_gaming_launch_url,
    map_push_gaming_action,
    verify_push_gaming_signature,
)


class PushGamingAdapter(BaseSupplierAdapter):
    """Adapter for Push Gaming's SEAMLESS wallet API."""

    supplier_id = "push_gaming"

    def __init__(self, config: PushGamingConfig | Any) -> None:
        super().__init__(config)
        if isinstance(config, PushGamingConfig):
            self._pg_config: PushGamingConfig = config
        else:
            self._pg_config = PushGamingConfig.model_validate(config.model_dump())

    def _do_launch(self, request: LaunchRequest, correlation_id: str) -> LaunchResult:
        session_id = str(uuid.uuid4())
        game_url = build_push_gaming_launch_url(
            request=request,
            casino_id=self._pg_config.casino_id,
            api_base_url=self._pg_config.api_base_url,
            session_id=session_id,
        )
        return LaunchResult(
            session_id=session_id,
            game_url=game_url,
            token=request.player.session_token,
            expires_at=self._utcnow() + timedelta(hours=4),
            metadata={"game_ref": request.game_id},
        )

    def _do_get_balance(self, session_id: str) -> WalletSnapshot:
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
        balance = self._do_get_balance(session_id)
        if balance.total_balance < amount:
            raise InsufficientFundsError(
                message="Insufficient funds for Push Gaming debit",
                requested_amount=str(amount),
                available_balance=str(balance.total_balance),
            )
        updated = balance.with_cash_delta(-amount)
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
        balance = self._do_get_balance(session_id)
        return TransactionResult(
            transaction_id=self._new_transaction_id(),
            supplier_ref=original_ref,
            balance_after=balance,
        )

    def _do_end_session(self, session_id: str) -> None:
        self._logger.debug("push_gaming.end_session", extra={"session_id": session_id})

    # ------------------------------------------------------------------
    # Inbound handlers
    # ------------------------------------------------------------------

    def handle_debit(
        self,
        req: PushGamingDebitRequest,
        raw_body: bytes = b"",
        signature: str = "",
    ) -> PushGamingWalletResponse:
        if self._pg_config.api_secret and raw_body:
            if not verify_push_gaming_signature(raw_body, signature, self._pg_config.api_secret):
                raise GameServiceError(message="Push Gaming signature verification failed")
        action_code = map_push_gaming_action(req.actionType)
        command = RoundCommand(
            command_type=CommandType.DEBIT,
            round_id=req.roundRef,
            amount=Decimal(req.betAmount),
            action_code=action_code,
            supplier_ref=req.txRef,
        )
        try:
            result = self.debit(req.sessionToken, req.roundRef, Decimal(req.betAmount), command)
        except InsufficientFundsError:
            return PushGamingWalletResponse(
                status="INSUFFICIENT_FUNDS",
                balance="0",
                txId="",
                currency=req.currency,
            )
        return PushGamingWalletResponse(
            balance=str(result.balance_after.cash_balance),
            bonus=str(result.balance_after.bonus_balance),
            txId=result.transaction_id,
            currency=req.currency,
        )

    def handle_credit(self, req: PushGamingCreditRequest) -> PushGamingWalletResponse:
        action_code = map_push_gaming_action("WIN")
        command = RoundCommand(
            command_type=CommandType.CREDIT,
            round_id=req.roundRef,
            amount=Decimal(req.winAmount),
            action_code=action_code,
            supplier_ref=req.txRef,
        )
        result = self.credit(req.sessionToken, req.roundRef, Decimal(req.winAmount), command)
        return PushGamingWalletResponse(
            balance=str(result.balance_after.cash_balance),
            bonus=str(result.balance_after.bonus_balance),
            txId=result.transaction_id,
            currency=req.currency,
        )

    def handle_rollback(self, req: PushGamingRollbackRequest) -> PushGamingWalletResponse:
        result = self.rollback(req.sessionToken, req.roundRef, req.txRef)
        balance = self._do_get_balance(req.sessionToken)
        return PushGamingWalletResponse(
            balance=str(balance.cash_balance),
            txId=result.transaction_id,
            currency=req.currency,
        )


__all__ = ["PushGamingAdapter"]

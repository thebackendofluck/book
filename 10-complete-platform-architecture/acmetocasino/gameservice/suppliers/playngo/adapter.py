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
gameservice.suppliers.playngo.adapter — Play'n GO Adapter
==========================================================

SEAMLESS wallet integration for Play'n GO slots.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

from acmetocasino.gameservice.errors import InsufficientFundsError
from acmetocasino.gameservice.models.enums import CommandType
from acmetocasino.gameservice.models.launch_request import LaunchRequest
from acmetocasino.gameservice.models.round_command import RoundCommand
from acmetocasino.gameservice.models.wallet_snapshot import WalletSnapshot
from acmetocasino.gameservice.suppliers.base import (
    BaseSupplierAdapter,
    LaunchResult,
    TransactionResult,
)
from acmetocasino.gameservice.suppliers.playngo.config import PlayngoConfig
from acmetocasino.gameservice.suppliers.playngo.models import (
    PlayngoCreditRequest,
    PlayngoDebitRequest,
    PlayngoRollbackRequest,
    PlayngoWalletResponse,
)
from acmetocasino.gameservice.suppliers.playngo.translator import (
    build_playngo_launch_url,
    map_playngo_action,
)


class PlayngoAdapter(BaseSupplierAdapter):
    """Adapter for Play'n GO's SEAMLESS wallet integration."""

    supplier_id = "playngo"

    def __init__(self, config: PlayngoConfig | Any) -> None:
        super().__init__(config)
        if isinstance(config, PlayngoConfig):
            self._png_config: PlayngoConfig = config
        else:
            self._png_config = PlayngoConfig.model_validate(config.model_dump())

    def _do_launch(self, request: LaunchRequest, correlation_id: str) -> LaunchResult:
        session_id = str(uuid.uuid4())
        endpoint = self._png_config.endpoint or self._png_config.api_base_url
        game_url = build_playngo_launch_url(
            request=request,
            partner_id=self._png_config.partner_id,
            endpoint=endpoint,
            session_id=session_id,
        )
        return LaunchResult(
            session_id=session_id,
            game_url=game_url,
            token=request.player.session_token,
            expires_at=self._utcnow() + timedelta(hours=4),
            metadata={"game_id": request.game_id, "partner_id": self._png_config.partner_id},
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
                message="Insufficient funds for Play'n GO debit",
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
        self._logger.debug("playngo.end_session", extra={"session_id": session_id})

    # ------------------------------------------------------------------
    # Inbound SEAMLESS handlers
    # ------------------------------------------------------------------

    def handle_debit(self, req: PlayngoDebitRequest) -> PlayngoWalletResponse:
        action_code = map_playngo_action(req.actionType, req.freeRound)
        command = RoundCommand(
            command_type=CommandType.DEBIT,
            round_id=req.roundId,
            amount=Decimal(req.amount),
            action_code=action_code,
            supplier_ref=req.transactionId,
        )
        try:
            result = self.debit(req.sessionToken, req.roundId, Decimal(req.amount), command)
        except InsufficientFundsError:
            return PlayngoWalletResponse(
                status=3,
                balance="0",
                transactionId="",
                currency=req.currency,
            )
        return PlayngoWalletResponse(
            balance=str(result.balance_after.cash_balance),
            bonus=str(result.balance_after.bonus_balance),
            transactionId=result.transaction_id,
            currency=req.currency,
        )

    def handle_credit(self, req: PlayngoCreditRequest) -> PlayngoWalletResponse:
        action_code = map_playngo_action(req.actionType)
        command = RoundCommand(
            command_type=CommandType.CREDIT,
            round_id=req.roundId,
            amount=Decimal(req.amount),
            action_code=action_code,
            supplier_ref=req.transactionId,
        )
        result = self.credit(req.sessionToken, req.roundId, Decimal(req.amount), command)
        return PlayngoWalletResponse(
            balance=str(result.balance_after.cash_balance),
            bonus=str(result.balance_after.bonus_balance),
            transactionId=result.transaction_id,
            currency=req.currency,
        )

    def handle_rollback(self, req: PlayngoRollbackRequest) -> PlayngoWalletResponse:
        result = self.rollback(req.sessionToken, req.roundId, req.transactionId)
        balance = self._do_get_balance(req.sessionToken)
        return PlayngoWalletResponse(
            balance=str(balance.cash_balance),
            bonus=str(balance.bonus_balance),
            transactionId=result.transaction_id,
            currency=req.currency,
        )


__all__ = ["PlayngoAdapter"]

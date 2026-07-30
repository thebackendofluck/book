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
gameservice.suppliers.nyx.adapter — NYX Adapter
================================================

SEAMLESS wallet integration for the NYX / Scientific Games game aggregator.

Multi-studio routing
--------------------
NYX routes game traffic from many studios through a single integration point.
The ``studioId`` field on callbacks identifies the originating studio.

In production, the platform uses ``studioId`` to:
* Apply studio-specific wagering contribution rates for bonuses.
* Filter game availability by studio for regulatory compliance.
* Route regulatory reports to the correct studio's certificate.
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
from acmetocasino.gameservice.suppliers.nyx.config import NYXConfig
from acmetocasino.gameservice.suppliers.nyx.models import (
    NYXCreditRequest,
    NYXDebitRequest,
    NYXRollbackRequest,
    NYXWalletResponse,
)
from acmetocasino.gameservice.suppliers.nyx.translator import (
    build_nyx_launch_url,
    map_nyx_action,
)


class NYXAdapter(BaseSupplierAdapter):
    """Adapter for NYX / Scientific Games SEAMLESS aggregator wallet API."""

    supplier_id = "nyx"

    def __init__(self, config: NYXConfig | Any) -> None:
        super().__init__(config)
        if isinstance(config, NYXConfig):
            self._nyx_config: NYXConfig = config
        else:
            self._nyx_config = NYXConfig.model_validate(config.model_dump())

    def _do_launch(self, request: LaunchRequest, correlation_id: str) -> LaunchResult:
        session_id = str(uuid.uuid4())
        game_url = build_nyx_launch_url(
            request=request,
            operator_id=self._nyx_config.operator_id,
            api_base_url=self._nyx_config.api_base_url,
            session_id=session_id,
        )
        return LaunchResult(
            session_id=session_id,
            game_url=game_url,
            token=request.player.session_token,
            expires_at=self._utcnow() + timedelta(hours=4),
            metadata={"game_id": request.game_id, "operator_id": self._nyx_config.operator_id},
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
                message="Insufficient funds for NYX debit",
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
        self._logger.debug("nyx.end_session", extra={"session_id": session_id})

    # ------------------------------------------------------------------
    # Inbound SEAMLESS handlers
    # ------------------------------------------------------------------

    def handle_debit(self, req: NYXDebitRequest) -> NYXWalletResponse:
        action_code = map_nyx_action(req.isFreeRound)
        command = RoundCommand(
            command_type=CommandType.DEBIT,
            round_id=req.roundId,
            amount=Decimal(req.amount),
            action_code=action_code,
            supplier_ref=req.txId,
            metadata={"studio_id": req.studioId} if req.studioId else {},
        )
        try:
            result = self.debit(req.sessionToken, req.roundId, Decimal(req.amount), command)
        except InsufficientFundsError:
            return NYXWalletResponse(
                status="INSUFFICIENT_FUNDS",
                balance="0",
                txId="",
                currency=req.currency,
            )
        return NYXWalletResponse(
            balance=str(result.balance_after.cash_balance),
            bonus=str(result.balance_after.bonus_balance),
            txId=result.transaction_id,
            currency=req.currency,
        )

    def handle_credit(self, req: NYXCreditRequest) -> NYXWalletResponse:
        command = RoundCommand(
            command_type=CommandType.CREDIT,
            round_id=req.roundId,
            amount=Decimal(req.amount),
            action_code=map_nyx_action(False),
            supplier_ref=req.txId,
            metadata={"studio_id": req.studioId} if req.studioId else {},
        )
        result = self.credit(req.sessionToken, req.roundId, Decimal(req.amount), command)
        return NYXWalletResponse(
            balance=str(result.balance_after.cash_balance),
            bonus=str(result.balance_after.bonus_balance),
            txId=result.transaction_id,
            currency=req.currency,
        )

    def handle_rollback(self, req: NYXRollbackRequest) -> NYXWalletResponse:
        result = self.rollback(req.sessionToken, req.roundId, req.txId)
        balance = self._do_get_balance(req.sessionToken)
        return NYXWalletResponse(
            balance=str(balance.cash_balance),
            txId=result.transaction_id,
            currency=req.currency,
        )


__all__ = ["NYXAdapter"]

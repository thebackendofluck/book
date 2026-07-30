# Companion code for "The Backend of Luck" - Chapter 10, Complete Platform Architecture.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Betgenius PUSH-style sportsbook adapter."""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

from acmetocasino.gameservice.models.enums import CommandType
from acmetocasino.gameservice.models.launch_request import LaunchRequest
from acmetocasino.gameservice.models.round_command import RoundCommand
from acmetocasino.gameservice.models.wallet_snapshot import WalletSnapshot
from acmetocasino.gameservice.suppliers.base import (
    BaseSupplierAdapter,
    LaunchResult,
    TransactionResult,
)
from acmetocasino.gameservice.suppliers.betgenius.config import BetgeniusConfig
from acmetocasino.gameservice.suppliers.betgenius.models import BetgeniusWalletEvent
from acmetocasino.gameservice.suppliers.betgenius.translator import (
    event_to_round_command,
)


class BetgeniusAdapter(BaseSupplierAdapter):
    """Adapter for Betgenius pushed sports and virtual-sports events."""

    supplier_id = "betgenius"

    def __init__(self, config: BetgeniusConfig | Any) -> None:
        super().__init__(config)
        if isinstance(config, BetgeniusConfig):
            self._betgenius_config = config
        else:
            self._betgenius_config = BetgeniusConfig.model_validate(
                config.model_dump()
            )

    def _do_launch(
        self, request: LaunchRequest, correlation_id: str
    ) -> LaunchResult:
        session_id = str(uuid.uuid4())
        base_url = self._betgenius_config.launch_url.rstrip("/")
        game_url = (
            f"{base_url}/launch"
            f"?token={request.player.session_token}"
            f"&jurisdiction={request.player.jurisdiction}"
            f"&currency={request.player.currency}"
        )
        return LaunchResult(
            session_id=session_id,
            game_url=game_url,
            token=request.player.session_token,
            expires_at=self._utcnow() + timedelta(hours=8),
            metadata={
                "correlation_id": correlation_id,
                "product": "sportsbook",
            },
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
        if balance.cash_balance < amount:
            from acmetocasino.gameservice.errors import InsufficientFundsError

            raise InsufficientFundsError(
                message="Insufficient funds for Betgenius event",
                requested_amount=str(amount),
                available_balance=str(balance.cash_balance),
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
        updated = self._do_get_balance(session_id).with_cash_delta(amount)
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
        return TransactionResult(
            transaction_id=self._new_transaction_id(),
            supplier_ref=original_ref,
            balance_after=self._do_get_balance(session_id),
        )

    def _do_end_session(self, session_id: str) -> None:
        self._logger.debug("betgenius.end_session", extra={"session_id": session_id})

    def handle_wallet_event(
        self,
        event: BetgeniusWalletEvent,
        session_id: str,
    ) -> TransactionResult:
        """Apply a signed Betgenius pushed wallet event idempotently."""

        command = event_to_round_command(event)
        amount = Decimal(event.amount)
        if command.command_type == CommandType.DEBIT:
            return self.debit(session_id, event.roundId, amount, command)
        if command.command_type == CommandType.CREDIT:
            return self.credit(session_id, event.roundId, amount, command)
        return self.rollback(session_id, event.roundId, event.supplier_ref)


__all__ = ["BetgeniusAdapter"]

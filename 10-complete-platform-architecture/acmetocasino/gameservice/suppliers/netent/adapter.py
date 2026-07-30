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
gameservice.suppliers.netent.adapter — NetEnt Adapter
======================================================

SEAMLESS wallet integration for NetEnt slots.

Multi-credit rounds
-------------------
NetEnt is known for sending multiple consecutive credit callbacks for the same
``roundId``.  This occurs when a slot game has layered features (e.g. a base
win followed by free spins triggered mid-round).

The adapter accumulates credits in ``_open_rounds`` until ``roundEnded=true``
is received.  The round is then closed and the full accumulated win is applied
to the wallet as a single credit.

This pattern avoids database race conditions when multiple partial credits
arrive in rapid succession.
"""

from __future__ import annotations

import threading
import uuid
from collections import defaultdict
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
from acmetocasino.gameservice.suppliers.netent.config import NetEntConfig
from acmetocasino.gameservice.suppliers.netent.models import (
    NetEntCreditRequest,
    NetEntDebitRequest,
    NetEntRollbackRequest,
    NetEntWalletResponse,
)
from acmetocasino.gameservice.suppliers.netent.translator import (
    build_netent_launch_url,
    map_action_type,
    parse_netent_amount,
)


class NetEntAdapter(BaseSupplierAdapter):
    """Adapter for NetEnt's SEAMLESS wallet service.

    Handles the multi-credit-per-round quirk by accumulating partial wins
    before committing the full round payout.

    Parameters
    ----------
    config:
        :class:`~acmetocasino.gameservice.suppliers.netent.config.NetEntConfig`.
    """

    supplier_id = "netent"

    def __init__(self, config: NetEntConfig | Any) -> None:
        super().__init__(config)
        if isinstance(config, NetEntConfig):
            self._ne_config: NetEntConfig = config
        else:
            self._ne_config = NetEntConfig.model_validate(config.model_dump())

        # Accumulated credits per (session_id, round_id)
        self._open_rounds: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
        self._rounds_lock = threading.Lock()

    # ------------------------------------------------------------------
    # SupplierAdapter implementation
    # ------------------------------------------------------------------

    def _do_launch(
        self, request: LaunchRequest, correlation_id: str
    ) -> LaunchResult:
        """Build the NetEnt game launch URL."""
        session_id = str(uuid.uuid4())
        game_url = build_netent_launch_url(
            request=request,
            casino_id=self._ne_config.casino_id,
            game_server_url=(
                self._ne_config.game_server_url or self._ne_config.api_base_url
            ),
            session_id=session_id,
        )
        return LaunchResult(
            session_id=session_id,
            game_url=game_url,
            token=request.player.session_token,
            expires_at=self._utcnow() + timedelta(hours=4),
            metadata={"game_id": request.game_id, "casino_id": self._ne_config.casino_id},
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
                message="Insufficient funds for NetEnt debit",
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
        # Clear any accumulated partial credits for this round
        with self._rounds_lock:
            self._open_rounds.pop((session_id, round_id), None)
        balance = self._do_get_balance(session_id)
        return TransactionResult(
            transaction_id=self._new_transaction_id(),
            supplier_ref=original_ref,
            balance_after=balance,
        )

    def _do_end_session(self, session_id: str) -> None:
        self._logger.debug("netent.end_session", extra={"session_id": session_id})

    # ------------------------------------------------------------------
    # Inbound SEAMLESS callback handlers
    # ------------------------------------------------------------------

    def handle_debit(self, req: NetEntDebitRequest) -> NetEntWalletResponse:
        """Process a NetEnt bet callback."""
        amount = parse_netent_amount(req.amount)
        action_code = map_action_type(req.actionType)
        command = RoundCommand(
            command_type=CommandType.DEBIT,
            round_id=req.roundId,
            amount=amount,
            action_code=action_code,
            supplier_ref=req.transactionId,
        )
        result = self.debit(req.casinoSessionId, req.roundId, amount, command)
        return NetEntWalletResponse(
            balance=str(result.balance_after.cash_balance),
            bonus=str(result.balance_after.bonus_balance),
            transactionId=result.transaction_id,
            currency=req.currency,
        )

    def handle_credit(self, req: NetEntCreditRequest) -> NetEntWalletResponse:
        """Process a NetEnt win callback, accumulating partial wins.

        If ``roundEnded=False``, the credit is accumulated but not yet committed.
        On ``roundEnded=True``, the total accumulated win is applied.
        """
        amount = parse_netent_amount(req.amount)
        key = (req.casinoSessionId, req.roundId)

        with self._rounds_lock:
            self._open_rounds[key] += amount
            if not req.roundEnded:
                # Partial credit — accumulate and return current balance
                balance = self._do_get_balance(req.casinoSessionId)
                return NetEntWalletResponse(
                    balance=str(balance.cash_balance),
                    bonus=str(balance.bonus_balance),
                    transactionId=self._new_transaction_id(),
                    currency=req.currency,
                )
            # Final credit — apply total
            total_win = self._open_rounds.pop(key, Decimal("0"))

        action_code = map_action_type(req.actionType)
        command = RoundCommand(
            command_type=CommandType.CREDIT,
            round_id=req.roundId,
            amount=total_win,
            action_code=action_code,
            supplier_ref=req.transactionId,
        )
        result = self.credit(req.casinoSessionId, req.roundId, total_win, command)
        return NetEntWalletResponse(
            balance=str(result.balance_after.cash_balance),
            bonus=str(result.balance_after.bonus_balance),
            transactionId=result.transaction_id,
            currency=req.currency,
        )

    def handle_rollback(self, req: NetEntRollbackRequest) -> NetEntWalletResponse:
        """Process a NetEnt rollback callback."""
        result = self.rollback(req.casinoSessionId, req.roundId, req.transactionId)
        return NetEntWalletResponse(
            balance=str(result.balance_after.cash_balance),
            bonus=str(result.balance_after.bonus_balance),
            transactionId=result.transaction_id,
            currency=req.currency,
        )


__all__ = ["NetEntAdapter"]

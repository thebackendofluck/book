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
gameservice.suppliers.kambi.adapter — Kambi Sportsbook Adapter
===============================================================

PULL-style integration for Kambi's sportsbook platform.

Sportsbook integration differences
------------------------------------
Sportsbook adapters differ from casino game adapters in several key ways:

1. **No game launch URL**: Kambi is a full-page sportsbook client embedded via
   iframe.  The ``launch_session`` method returns the Kambi client URL.
2. **Bet placement is asynchronous**: The stake debit and win credit are
   separated in time by hours or days.
3. **Settlement is polled**: The platform runs a background job that calls
   :meth:`process_settlement_feed` on a configurable interval.
4. **Cash-out is player-initiated**: The player can request cash-out via the
   Kambi UI; the platform must honour it via :meth:`process_cash_out`.

PULL settlement flow
---------------------
1. Player places bet → Kambi notifies platform via receipt webhook.
2. Platform applies stake debit at bet placement (handled by receipt handler).
3. Background job polls ``/settlement`` every 60 seconds.
4. For each settled bet, the platform applies a credit (for wins) or no-op
   (for losses, since the debit was already applied).
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

from acmetocasino.gameservice.errors import GameServiceError
from acmetocasino.gameservice.models.enums import ActionCode, CommandType
from acmetocasino.gameservice.models.launch_request import LaunchRequest
from acmetocasino.gameservice.models.round_command import RoundCommand
from acmetocasino.gameservice.models.wallet_snapshot import WalletSnapshot
from acmetocasino.gameservice.suppliers.base import (
    BaseSupplierAdapter,
    LaunchResult,
    TransactionResult,
)
from acmetocasino.gameservice.suppliers.kambi.config import KambiConfig
from acmetocasino.gameservice.suppliers.kambi.models import (
    KambiBetReceiptEvent,
    KambiSettlementEvent,
)
from acmetocasino.gameservice.suppliers.kambi.translator import (
    build_balance_response,
    settlement_to_round_command,
)


class KambiAdapter(BaseSupplierAdapter):
    """Adapter for Kambi's PULL-style sportsbook integration.

    Parameters
    ----------
    config:
        :class:`~acmetocasino.gameservice.suppliers.kambi.config.KambiConfig`.
    """

    supplier_id = "kambi"

    def __init__(self, config: KambiConfig | Any) -> None:
        super().__init__(config)
        if isinstance(config, KambiConfig):
            self._kambi_config: KambiConfig = config
        else:
            self._kambi_config = KambiConfig.model_validate(config.model_dump())

    # ------------------------------------------------------------------
    # SupplierAdapter implementation
    # ------------------------------------------------------------------

    def _do_launch(
        self, request: LaunchRequest, correlation_id: str
    ) -> LaunchResult:
        """Return the Kambi sportsbook client URL.

        Kambi does not require a session creation API call — the client URL
        is constructed from the brand's offering URL and the session token.
        """
        session_id = str(uuid.uuid4())
        offering = self._kambi_config.offering_url
        base = self._kambi_config.api_base_url

        client_url = (
            f"{base}/{offering}/#/"
            f"?token={request.player.session_token}"
            f"&lang={request.player.language}"
            f"&market={request.player.jurisdiction}"
            f"&currency={request.player.currency}"
        )

        return LaunchResult(
            session_id=session_id,
            game_url=client_url,
            token=request.player.session_token,
            expires_at=self._utcnow() + timedelta(hours=8),
            metadata={
                "offering_url": offering,
                "brand_id": self._kambi_config.brand_id,
            },
        )

    def _do_get_balance(self, session_id: str) -> WalletSnapshot:
        """Return the player wallet balance (queried by Kambi before bet placement)."""
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
        """Apply a stake debit when a bet is placed."""
        balance = self._do_get_balance(session_id)
        if balance.cash_balance < amount:
            from acmetocasino.gameservice.errors import InsufficientFundsError
            raise InsufficientFundsError(
                message="Insufficient funds for Kambi bet placement",
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
        """Apply a win payout when a bet settles as a win."""
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
        """Reverse a bet stake (e.g. bet voided after placement)."""
        balance = self._do_get_balance(session_id)
        return TransactionResult(
            transaction_id=self._new_transaction_id(),
            supplier_ref=original_ref,
            balance_after=balance,
        )

    def _do_end_session(self, session_id: str) -> None:
        self._logger.debug("kambi.end_session", extra={"session_id": session_id})

    # ------------------------------------------------------------------
    # Kambi-specific: settlement feed processing
    # ------------------------------------------------------------------

    def handle_bet_receipt(
        self,
        event: KambiBetReceiptEvent,
        session_id: str,
    ) -> TransactionResult:
        """Apply the stake debit when Kambi confirms a bet placement.

        Called by the webhook controller on the ``/webhooks/kambi/receipt``
        endpoint.

        Parameters
        ----------
        event:
            Validated Kambi bet receipt event.
        session_id:
            Platform session for this player.
        """
        amount = Decimal(event.stake)
        command = RoundCommand(
            command_type=CommandType.DEBIT,
            round_id=event.betId,
            amount=amount,
            action_code=ActionCode.REGULAR,
            supplier_ref=f"kambi-bet-{event.betId}",
        )
        return self.debit(session_id, event.betId, amount, command)

    def process_settlement_feed(
        self,
        events: list[KambiSettlementEvent],
        session_id: str,
    ) -> list[TransactionResult]:
        """Process a batch of settled bets from Kambi's settlement feed.

        This method is called by the platform's background settlement job.
        For each event, a credit is applied for wins and no-op for losses.

        Parameters
        ----------
        events:
            List of :class:`KambiSettlementEvent` items from the feed.
        session_id:
            Platform session for the player associated with these bets.

        Returns
        -------
        list[TransactionResult]
            One result per event that produced a financial operation.
        """
        results: list[TransactionResult] = []
        for event in events:
            command = settlement_to_round_command(event)
            if command is None:
                continue  # VOID or LOSS — no financial action needed
            if command.command_type == CommandType.CREDIT:
                result = self.credit(
                    session_id, event.betId, command.amount, command
                )
                results.append(result)
        return results

    def process_cash_out(
        self,
        bet_id: str,
        cash_out_value: Decimal,
        session_id: str,
        currency: str,
    ) -> TransactionResult:
        """Apply a player-initiated cash-out.

        The cash-out value is credited to the player's wallet.  The original
        bet stake debit is NOT reversed (net effect: player receives cash_out_value,
        which is less than the full potential win but more than zero).

        Parameters
        ----------
        bet_id:
            Kambi bet ID being cashed out.
        cash_out_value:
            The cash-out value agreed with Kambi.
        session_id:
            Platform session for this player.
        currency:
            ISO-4217 currency code.
        """
        command = RoundCommand(
            command_type=CommandType.CREDIT,
            round_id=bet_id,
            amount=cash_out_value,
            action_code=ActionCode.REGULAR,
            supplier_ref=f"kambi-cashout-{bet_id}",
        )
        return self.credit(session_id, bet_id, cash_out_value, command)

    def get_balance_for_kambi(self, session_id: str) -> dict[str, str]:
        """Return balance in Kambi's expected format for their balance query."""
        snapshot = self.get_balance(session_id)
        response = build_balance_response(snapshot)
        return response.model_dump()


__all__ = ["KambiAdapter"]

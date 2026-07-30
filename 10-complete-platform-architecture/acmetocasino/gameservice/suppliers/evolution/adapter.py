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
gameservice.suppliers.evolution.adapter — Evolution Gaming Adapter
===================================================================

Implements the :class:`SupplierAdapter` protocol for Evolution Gaming's
live-casino platform.

Integration pattern: PUSH
--------------------------
Evolution POSTs signed webhook events to the platform rather than the platform
making real-time outbound wallet calls.  This adapter therefore has two
responsibilities:

1. **Session launch**: Make an outbound REST call to Evolution's ``entry``
   endpoint to obtain a game URL.

2. **Webhook handling**: Accept inbound events from the platform's webhook
   controller (which handles the HTTP routing) and apply them to the wallet.
   The :meth:`handle_webhook` method is NOT part of the base
   :class:`SupplierAdapter` protocol — it is Evolution-specific and called
   by the ``/api/webhooks/evolution`` endpoint handler.

Simulated API calls
-------------------
All outbound HTTP calls in this module are *simulated* — no real network
traffic is generated.  In production, replace the ``_simulate_*`` methods
with actual ``httpx`` (or ``requests``) calls.  The structure mirrors the real
Evolution API exactly.

Live-casino specifics
---------------------
* **Demo mode**: Evolution does NOT support demo sessions for live games.
  The adapter raises an error if ``GameMode.DEMO`` is requested.
* **Tipping**: The ``TIP`` webhook event uses ``CommandType.TIP``, which the
  platform routes to a dedicated debit path (no round association).
* **Multi-seat**: ``extra_params["seat_ids"]`` carries a comma-separated list
  of seat IDs when the player occupies multiple seats.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from acmetocasino.gameservice.errors import GameServiceError, InvalidSessionError
from acmetocasino.gameservice.models.enums import CommandType, GameMode
from acmetocasino.gameservice.models.launch_request import LaunchRequest
from acmetocasino.gameservice.models.round_command import RoundCommand
from acmetocasino.gameservice.models.wallet_snapshot import WalletSnapshot
from acmetocasino.gameservice.suppliers.base import (
    BaseSupplierAdapter,
    LaunchResult,
    TransactionResult,
)
from acmetocasino.gameservice.suppliers.evolution.config import EvolutionConfig
from acmetocasino.gameservice.suppliers.evolution.models import (
    EvolutionWebhookEvent,
    EvolutionWebhookResponse,
)
from acmetocasino.gameservice.suppliers.evolution.translator import (
    build_session_request,
    cents_to_decimal,
    decimal_to_cents,
    parse_webhook_command_type,
    session_expiry,
    verify_webhook_signature,
)


class EvolutionAdapter(BaseSupplierAdapter):
    """Adapter for Evolution Gaming's live-casino platform.

    This adapter handles session launch via Evolution's REST API and processes
    inbound PUSH events via :meth:`handle_webhook`.

    Parameters
    ----------
    config:
        An :class:`~acmetocasino.gameservice.suppliers.evolution.config.EvolutionConfig`
        instance containing credentials and endpoint configuration.
    """

    supplier_id = "evolution"

    def __init__(self, config: EvolutionConfig | Any) -> None:
        super().__init__(config)
        if isinstance(config, EvolutionConfig):
            self._evo_config: EvolutionConfig = config
        else:
            # Accept plain SupplierConfig as fallback (used in tests)
            self._evo_config = EvolutionConfig.model_validate(config.model_dump())

    # ------------------------------------------------------------------
    # SupplierAdapter implementation
    # ------------------------------------------------------------------

    def _do_launch(
        self, request: LaunchRequest, correlation_id: str
    ) -> LaunchResult:
        """Launch a live-casino session via Evolution's entry endpoint.

        Evolution does not support demo mode.  Raises
        :class:`~acmetocasino.gameservice.errors.GameServiceError` if demo
        mode is requested.
        """
        if request.mode == GameMode.DEMO:
            raise GameServiceError(
                message=(
                    "Evolution Gaming does not support demo sessions for live games. "
                    "Redirect the player to a slots demo instead."
                )
            )

        session_id = str(uuid.uuid4())
        request_uuid = str(uuid.uuid4())

        session_req = build_session_request(request, session_id, request_uuid)
        self._logger.debug(
            "evolution.launch.request",
            extra={"table_id": session_req.config.game.table.id},
        )

        # Simulate the Evolution session creation API call
        entry_url = self._simulate_create_session(session_req)

        return LaunchResult(
            session_id=session_id,
            game_url=entry_url,
            token=request.player.session_token,
            expires_at=session_expiry(minutes=60),
            metadata={
                "table_id": session_req.config.game.table.id,
                "environment": self._evo_config.environment,
                "multi_seat": request.extra_params.get("seat_ids", ""),
            },
        )

    def _do_get_balance(self, session_id: str) -> WalletSnapshot:
        """Return the player balance for the session.

        In PUSH integrations the balance is not queried outbound — it is
        returned in the webhook response.  This method is retained for
        consistency with the protocol contract and for use by reconciliation
        tooling.
        """
        # In a real implementation: look up the session in the session store
        # and query the wallet service.  Here we return a stub.
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
        """Apply a debit from an inbound Evolution DEBIT or TIP event."""
        balance = self._do_get_balance(session_id)
        new_cash = balance.cash_balance - amount
        if new_cash < Decimal("0"):
            from acmetocasino.gameservice.errors import InsufficientFundsError
            raise InsufficientFundsError(
                message="Insufficient funds for Evolution debit",
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
        """Apply a credit from an inbound Evolution CREDIT or PROMO event."""
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
        """Reverse a debit from an inbound Evolution CANCEL event."""
        balance = self._do_get_balance(session_id)
        # A rollback re-credits the original debit amount.
        # In a real implementation: look up the original transaction by round_id.
        return TransactionResult(
            transaction_id=self._new_transaction_id(),
            supplier_ref=original_ref,
            balance_after=balance,
        )

    def _do_end_session(self, session_id: str) -> None:
        """Terminate the Evolution session.

        Notifies the Evolution platform that the player has left the table.
        In PUSH integrations this is typically done via a separate REST call
        to Evolution's session termination endpoint.
        """
        self._logger.info(
            "evolution.end_session",
            extra={"session_id": session_id},
        )
        # Simulate session termination API call
        self._simulate_end_session(session_id)

    # ------------------------------------------------------------------
    # Evolution-specific: webhook handler
    # ------------------------------------------------------------------

    def handle_webhook(
        self,
        event: EvolutionWebhookEvent,
        raw_body: bytes,
        signature_header: str,
    ) -> EvolutionWebhookResponse:
        """Process an inbound Evolution push event.

        This method is called by the platform's inbound webhook controller
        when Evolution POSTs an event to the registered callback URL.

        Parameters
        ----------
        event:
            Parsed and validated webhook payload.
        raw_body:
            The raw HTTP request body bytes (for signature verification).
        signature_header:
            The ``X-Evo-Signature`` header value.

        Returns
        -------
        EvolutionWebhookResponse
            The response to return to Evolution.  Must be returned within
            5 seconds or Evolution will retry.

        Raises
        ------
        GameServiceError
            If the signature is invalid or the event type is unknown.
        """
        if self._evo_config.webhook_secret:
            if not verify_webhook_signature(
                raw_body, signature_header, self._evo_config.webhook_secret
            ):
                raise GameServiceError(
                    message="Evolution webhook signature verification failed",
                )

        cmd_type = parse_webhook_command_type(event.type)
        currency = "EUR"  # In production: derive from session lookup
        amount = cents_to_decimal(event.value, currency)

        command = RoundCommand(
            command_type=cmd_type,
            round_id=event.roundId,
            amount=amount,
            supplier_ref=event.transactionId,
        )

        if cmd_type in (CommandType.DEBIT, CommandType.TIP):
            result = self.debit(event.sid, event.roundId, amount, command)
        elif cmd_type == CommandType.CREDIT:
            result = self.credit(event.sid, event.roundId, amount, command)
        elif cmd_type == CommandType.ROLLBACK:
            result = self.rollback(event.sid, event.roundId, event.transactionId)
        else:
            raise GameServiceError(
                message=f"Unhandled Evolution event type: {event.type!r}"
            )

        balance_cents = decimal_to_cents(
            result.balance_after.cash_balance, currency
        )
        bonus_cents = decimal_to_cents(
            result.balance_after.bonus_balance, currency
        )

        return EvolutionWebhookResponse(
            status="OK",
            balance=balance_cents,
            bonus=bonus_cents,
            transactionId=result.transaction_id,
        )

    # ------------------------------------------------------------------
    # Simulated HTTP methods (replace with real httpx calls in production)
    # ------------------------------------------------------------------

    def _simulate_create_session(self, session_req: Any) -> str:
        """Simulate Evolution's session creation API and return a game URL."""
        table_id = session_req.config.game.table.id or "AutoRoulette1"
        base = self._evo_config.resolved_api_url
        token = str(uuid.uuid4())
        return (
            f"{base}/frontend/entry?"
            f"tableId={table_id}&token={token}"
            f"&casinoKey={self._evo_config.casino_key}"
        )

    def _simulate_end_session(self, session_id: str) -> None:
        """Simulate the Evolution session termination API call."""
        self._logger.debug(
            "evolution._simulate_end_session",
            extra={"session_id": session_id},
        )


__all__ = ["EvolutionAdapter"]

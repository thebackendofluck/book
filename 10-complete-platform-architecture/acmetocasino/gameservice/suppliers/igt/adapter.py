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
gameservice.suppliers.igt.adapter — IGT Adapter
================================================

PULL + SOAP integration for IGT's online and land-based game platform.

Settlement poll flow
---------------------
1. Platform background job calls :meth:`poll_settlement_feed` every N seconds.
2. The feed returns a list of :class:`IGTRoundClosedEvent` objects.
3. For each event, the adapter applies a debit (already applied at round start)
   and a credit for any win.  In IGT's model, the debit and credit are both
   included in the settlement event — the platform must check if the debit was
   already applied at round start to avoid double-debiting.

In production the round state machine in the session store handles this:
* If the debit for ``round_id`` is not yet recorded → apply debit + credit.
* If the debit for ``round_id`` is already recorded → apply credit only.

Jackpot pool integration
------------------------
IGT's MegaJackpots pool is queried via SOAP.  The adapter includes a
:meth:`query_jackpot_pool` method that calls the SOAP service and returns
the current pool values for display in the game UI.

When a jackpot wins, IGT notifies the platform with a special
``roundClosedEvent`` where ``winAmount`` includes the jackpot prize.
The adapter does not need special handling — the full ``winAmount`` is
credited as a single transaction.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

from acmetocasino.gameservice.errors import InsufficientFundsError
from acmetocasino.gameservice.models.enums import ActionCode, CommandType
from acmetocasino.gameservice.models.launch_request import LaunchRequest
from acmetocasino.gameservice.models.round_command import RoundCommand
from acmetocasino.gameservice.models.wallet_snapshot import WalletSnapshot
from acmetocasino.gameservice.suppliers.base import (
    BaseSupplierAdapter,
    LaunchResult,
    TransactionResult,
)
from acmetocasino.gameservice.suppliers.igt.config import IGTConfig
from acmetocasino.gameservice.suppliers.igt.models import (
    IGTJackpotPoolResponse,
    IGTRoundClosedEvent,
    IGTSoapRequest,
    IGTSoapResponse,
)
from acmetocasino.gameservice.suppliers.igt.translator import (
    build_igt_session_request,
    build_jackpot_soap_request,
    parse_round_closed_event,
    render_soap_envelope,
)


class IGTAdapter(BaseSupplierAdapter):
    """Adapter for IGT's PULL-style sportsbook and casino integration.

    Handles session launch, settlement polling, and progressive jackpot queries.
    Bridges between IGT's legacy SOAP API and the platform's REST-native domain.

    Parameters
    ----------
    config:
        :class:`~acmetocasino.gameservice.suppliers.igt.config.IGTConfig`.
    """

    supplier_id = "igt"

    def __init__(self, config: IGTConfig | Any) -> None:
        super().__init__(config)
        if isinstance(config, IGTConfig):
            self._igt_config: IGTConfig = config
        else:
            self._igt_config = IGTConfig.model_validate(config.model_dump())

    # ------------------------------------------------------------------
    # SupplierAdapter implementation
    # ------------------------------------------------------------------

    def _do_launch(self, request: LaunchRequest, correlation_id: str) -> LaunchResult:
        """Launch an IGT game session via the REST session API."""
        session_id = str(uuid.uuid4())
        session_req = build_igt_session_request(request, self._igt_config.system_id)
        self._logger.debug(
            "igt.launch.request",
            extra={"game_code": session_req.gameCode, "system_id": session_req.operatorId},
        )

        # Simulate IGT's REST session creation
        game_url = self._simulate_create_session(session_req, session_id)

        return LaunchResult(
            session_id=session_id,
            game_url=game_url,
            token=request.player.session_token,
            expires_at=self._utcnow() + timedelta(hours=8),
            metadata={
                "game_code": request.game_id,
                "system_id": self._igt_config.system_id,
                "jackpot_network_id": self._igt_config.jackpot_network_id,
            },
        )

    def _do_get_balance(self, session_id: str) -> WalletSnapshot:
        return WalletSnapshot(
            cash_balance=Decimal("0"),
            currency="USD",
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
                message="Insufficient funds for IGT debit",
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
        updated = balance.with_cash_delta(amount) if amount > Decimal("0") else balance
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
        self._logger.debug("igt.end_session", extra={"session_id": session_id})

    # ------------------------------------------------------------------
    # IGT-specific: PULL settlement feed
    # ------------------------------------------------------------------

    def poll_settlement_feed(self, session_id: str) -> list[IGTRoundClosedEvent]:
        """Poll IGT's settlement feed and return pending round-closed events.

        In production this makes an authenticated GET request to
        :attr:`IGTConfig.settlement_feed_url`.  Here we return an empty list
        to illustrate the polling pattern without simulating specific rounds.
        """
        self._logger.debug(
            "igt.poll_settlement_feed",
            extra={"feed_url": self._igt_config.settlement_feed_url},
        )
        # Simulate: return empty list (no pending settlements)
        return []

    def process_round_closed(
        self,
        event: IGTRoundClosedEvent,
        session_id: str,
    ) -> list[TransactionResult]:
        """Process a single IGT round-closed event.

        Applies both the debit (bet) and credit (win) for the settled round.
        The caller (background job) is responsible for deduplication — if the
        debit was already applied at round start, only the credit should be
        applied here.

        Parameters
        ----------
        event:
            A single round-closed event from the settlement feed.
        session_id:
            Platform session for the player.

        Returns
        -------
        list[TransactionResult]
            Between 0 and 2 results (debit and/or credit).
        """
        results: list[TransactionResult] = []
        bet_amount, win_amount = parse_round_closed_event(event)

        # Apply the debit (bet).  In production: check idempotency first.
        if bet_amount > Decimal("0"):
            debit_cmd = RoundCommand(
                command_type=CommandType.DEBIT,
                round_id=event.roundId,
                amount=bet_amount,
                action_code=ActionCode.REGULAR,
                supplier_ref=f"igt-bet-{event.roundId}",
            )
            debit_result = self.debit(session_id, event.roundId, bet_amount, debit_cmd)
            results.append(debit_result)

        # Apply the win credit (if any).
        if win_amount > Decimal("0"):
            credit_cmd = RoundCommand(
                command_type=CommandType.CREDIT,
                round_id=event.roundId,
                amount=win_amount,
                action_code=ActionCode.REGULAR,
                supplier_ref=f"igt-win-{event.roundId}",
            )
            credit_result = self.credit(session_id, event.roundId, win_amount, credit_cmd)
            results.append(credit_result)

        return results

    # ------------------------------------------------------------------
    # IGT-specific: progressive jackpot SOAP
    # ------------------------------------------------------------------

    def query_jackpot_pool(self) -> IGTJackpotPoolResponse:
        """Query the IGT MegaJackpots pool via the SOAP service.

        Returns the current pool values for all jackpot tiers (Mega, Major,
        Mini) in the configured network.

        In production: call the SOAP endpoint at
        :attr:`IGTConfig.soap_endpoint` using the rendered SOAP envelope.

        Returns
        -------
        IGTJackpotPoolResponse
            Current pool values.
        """
        soap_req = build_jackpot_soap_request(
            network_id=self._igt_config.jackpot_network_id,
            username=self._igt_config.soap_username,
            password=self._igt_config.soap_password,
        )
        envelope = render_soap_envelope(soap_req)
        self._logger.debug(
            "igt.query_jackpot_pool",
            extra={"network_id": self._igt_config.jackpot_network_id},
        )

        # Simulate SOAP response
        return self._simulate_jackpot_pool_response()

    # ------------------------------------------------------------------
    # Simulated API calls
    # ------------------------------------------------------------------

    def _simulate_create_session(self, session_req: Any, session_id: str) -> str:
        """Simulate IGT's REST session creation."""
        base = self._igt_config.api_base_url
        token = str(uuid.uuid4())
        return (
            f"{base}/launch/game?"
            f"gameCode={session_req.gameCode}"
            f"&sessionId={session_id}"
            f"&token={token}"
            f"&operatorId={self._igt_config.system_id}"
        )

    def _simulate_jackpot_pool_response(self) -> IGTJackpotPoolResponse:
        """Simulate the MegaJackpots SOAP response with placeholder values."""
        from acmetocasino.gameservice.suppliers.igt.models import IGTJackpotPool
        return IGTJackpotPoolResponse(
            networkId=self._igt_config.jackpot_network_id or "megajackpots",
            pools=[
                IGTJackpotPool(
                    poolId="mega",
                    name="Mega",
                    currentValue="1523847.50",
                    currency="USD",
                    seedValue="1000000.00",
                ),
                IGTJackpotPool(
                    poolId="major",
                    name="Major",
                    currentValue="49832.75",
                    currency="USD",
                    seedValue="25000.00",
                ),
                IGTJackpotPool(
                    poolId="mini",
                    name="Mini",
                    currentValue="1243.20",
                    currency="USD",
                    seedValue="500.00",
                ),
            ],
        )


__all__ = ["IGTAdapter"]

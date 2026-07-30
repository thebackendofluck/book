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
gameservice.suppliers.base — Supplier Adapter Contract
=======================================================

This module defines the **SupplierAdapter** protocol that every supplier
integration must satisfy, the shared value-types returned by adapter
operations, and ``BaseSupplierAdapter`` — a partial implementation that
provides cross-cutting concerns (logging, metrics, retries, error
translation) so concrete adapters only need to implement the
supplier-specific network logic.

Integration patterns
--------------------
There are three integration patterns in the iGaming industry:

SEAMLESS (most common)
    The supplier's game server calls the platform wallet API for every debit
    and credit.  The platform is the single source of truth for balances.
    Examples: Pragmatic Play, NetEnt, Play'n GO, Hacksaw.

PUSH
    The supplier pushes wallet events to platform-registered webhook
    endpoints.  The platform must validate and apply events idempotently.
    Examples: Evolution Gaming, Betgenius.

PULL
    The platform periodically queries the supplier for settlement data.
    Common for legacy systems or when real-time integration is impractical.
    Examples: Kambi, IGT.

All three patterns expose the same ``SupplierAdapter`` interface so the
platform core remains pattern-agnostic.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from acmetocasino.gameservice.errors import GameServiceError
from acmetocasino.gameservice.models.enums import CallbackStyle, ProductType
from acmetocasino.gameservice.models.launch_request import LaunchRequest
from acmetocasino.gameservice.models.round_command import RoundCommand
from acmetocasino.gameservice.models.wallet_snapshot import WalletSnapshot

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared value types
# ---------------------------------------------------------------------------


class LaunchResult(BaseModel):
    """The outcome of a successful game-session launch.

    Attributes
    ----------
    session_id:
        The platform-scoped session identifier (UUID).  This is stored in the
        session ledger and used to correlate all subsequent wallet calls.
    game_url:
        The fully-qualified URL the frontend should load in the iframe or
        navigate to in the native client.
    token:
        An opaque supplier-issued token embedded in ``game_url``.  Kept
        separately so the platform can revoke it without re-parsing the URL.
    expires_at:
        UTC datetime after which the ``game_url`` is no longer valid.
        Frontends should initiate a re-launch before this time.
    metadata:
        Optional supplier-specific data (e.g. table ID for live casino).
    """

    model_config = {"frozen": True}

    session_id: str = Field(..., description="Platform session UUID.")
    game_url: str = Field(..., description="Fully-qualified game launch URL.")
    token: str = Field(..., description="Opaque supplier session token.")
    expires_at: datetime = Field(..., description="UTC expiry for the game URL.")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Supplier-specific contextual data.",
    )


class TransactionResult(BaseModel):
    """The outcome of a wallet debit, credit, or rollback operation.

    Attributes
    ----------
    transaction_id:
        Platform-generated UUID for this transaction record.
    supplier_ref:
        The supplier's own reference echoed back (for reconciliation).
    balance_after:
        The player's wallet snapshot immediately after the operation.
    already_processed:
        ``True`` if idempotency detection determined this transaction was
        already applied.  The previous result is returned unchanged.
    """

    model_config = {"frozen": True}

    transaction_id: str = Field(..., description="Platform transaction UUID.")
    supplier_ref: str | None = Field(
        default=None,
        description="Supplier-provided transaction reference.",
    )
    balance_after: WalletSnapshot = Field(
        ..., description="Wallet state after the operation."
    )
    already_processed: bool = Field(
        default=False,
        description="True when the idempotency check fired.",
    )


@dataclass(frozen=True)
class SupplierCapabilities:
    """Declares the runtime capabilities of a single supplier integration.

    Each flag corresponds to a feature that can be queried at runtime to
    enable or disable platform-level behaviour (e.g. bonus campaign targeting,
    free-round award eligibility).

    Attributes
    ----------
    supplier_id:
        The unique identifier of the supplier.
    product_types:
        The :class:`~acmetocasino.gameservice.models.enums.ProductType` values
        this supplier offers.
    callback_style:
        The wallet-integration pattern used by this supplier.
    free_rounds:
        Supplier supports awarding and tracking free rounds natively.
    jackpots:
        Supplier contributes to or pays from a jackpot pool.
    tournaments:
        Supplier exposes a tournament/leaderboard API.
    live_betting:
        Supplier supports in-play sports betting (sportsbook only).
    cash_out:
        Supplier supports cash-out on open bets.
    tipping:
        Supplier supports live-dealer tipping (live casino only).
    multi_seat:
        Supplier supports multiple players at the same live table.
    bonus_buy:
        Supplier supports direct bonus-feature purchases.
    progressive_pools:
        Supplier participates in linked progressive jackpot pools.
    regulatory_reporting:
        Supplier exposes a dedicated regulatory-reporting endpoint.
    demo_available:
        Supplier permits play-money (demo) sessions.
    """

    supplier_id: str
    product_types: tuple[ProductType, ...] = field(default_factory=tuple)
    callback_style: CallbackStyle = CallbackStyle.SEAMLESS
    free_rounds: bool = False
    jackpots: bool = False
    tournaments: bool = False
    live_betting: bool = False
    cash_out: bool = False
    tipping: bool = False
    multi_seat: bool = False
    bonus_buy: bool = False
    progressive_pools: bool = False
    regulatory_reporting: bool = False
    demo_available: bool = True


@dataclass(frozen=True)
class SupplierInfo:
    """Lightweight descriptor returned by ``SupplierRegistry.list_available``.

    Attributes
    ----------
    supplier_id:
        Unique supplier key.
    display_name:
        Human-readable supplier name.
    capabilities:
        Full capability declaration for this supplier.
    is_enabled:
        Whether this supplier is currently enabled for the queried
        brand + jurisdiction combination.
    """

    supplier_id: str
    display_name: str
    capabilities: SupplierCapabilities
    is_enabled: bool = True


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class SupplierAdapter(Protocol):
    """The contract every supplier adapter must fulfil.

    This is a :pep:`544` ``Protocol`` rather than an abstract base class so
    that adapters can be developed and tested independently without any
    inheritance coupling to the platform core.

    All monetary values are :class:`~decimal.Decimal`.  Adapters must never
    use ``float`` for financial arithmetic.
    """

    supplier_id: str

    def launch_session(self, request: LaunchRequest) -> LaunchResult:
        """Build a game-launch URL and create a platform session record.

        Parameters
        ----------
        request:
            Validated launch parameters (player context, game ID, mode).

        Returns
        -------
        LaunchResult
            Contains the ``game_url`` the frontend should load.
        """
        ...

    def get_balance(self, session_id: str) -> WalletSnapshot:
        """Return the player's current wallet balance for the given session.

        Used by PULL-style integrations where the supplier queries balance
        before initiating a round.

        Parameters
        ----------
        session_id:
            The platform session identifier returned by :meth:`launch_session`.
        """
        ...

    def debit(
        self,
        session_id: str,
        round_id: str,
        amount: Decimal,
        command: RoundCommand,
    ) -> TransactionResult:
        """Apply a player wager (debit) to the wallet.

        Parameters
        ----------
        session_id:
            Platform session correlating this call to a player.
        round_id:
            Supplier-provided round identifier.
        amount:
            Wager amount in the player's currency.
        command:
            Full command context including action code and supplier ref.
        """
        ...

    def credit(
        self,
        session_id: str,
        round_id: str,
        amount: Decimal,
        command: RoundCommand,
    ) -> TransactionResult:
        """Apply a win payout (credit) to the wallet.

        Parameters
        ----------
        session_id:
            Platform session correlating this call to a player.
        round_id:
            Supplier-provided round identifier.
        amount:
            Win amount in the player's currency (may be zero — push result).
        command:
            Full command context including action code and supplier ref.
        """
        ...

    def rollback(
        self,
        session_id: str,
        round_id: str,
        original_ref: str,
    ) -> TransactionResult:
        """Reverse a previously applied debit.

        Called when a game round is cancelled or the game server fails before
        a credit is issued.  Must be idempotent.

        Parameters
        ----------
        session_id:
            Platform session identifier.
        round_id:
            The round whose debit should be reversed.
        original_ref:
            The ``supplier_ref`` of the original debit command.
        """
        ...

    def end_session(self, session_id: str) -> None:
        """Gracefully terminate the game session.

        Called when the player exits the game, their session expires, or a
        responsible-gambling intervention triggers session termination.

        Parameters
        ----------
        session_id:
            Platform session identifier to terminate.
        """
        ...


# ---------------------------------------------------------------------------
# Base implementation
# ---------------------------------------------------------------------------


class BaseSupplierAdapter:
    """Partial implementation providing cross-cutting concerns for all adapters.

    Concrete adapters extend this class and override the ``_do_*`` methods
    that contain supplier-specific logic.  This class handles:

    * Structured logging (request/response lifecycle)
    * Basic retry logic with exponential back-off
    * Error translation from supplier HTTP errors to domain exceptions
    * Correlation ID injection and propagation
    * Metrics instrumentation stubs (replace with real metrics client)

    Override contract
    -----------------
    Subclasses **must** override:

    * :meth:`_do_launch`
    * :meth:`_do_get_balance`
    * :meth:`_do_debit`
    * :meth:`_do_credit`
    * :meth:`_do_rollback`
    * :meth:`_do_end_session`

    Subclasses **may** override:

    * :meth:`_translate_error` — map supplier error codes to domain exceptions
    * :meth:`_should_retry`    — custom retry predicate
    """

    supplier_id: str = "base"

    def __init__(self, config: Any | None = None) -> None:
        self._config = config
        self._logger = logging.getLogger(
            f"acmetocasino.suppliers.{self.supplier_id}"
        )

    # ------------------------------------------------------------------
    # Public SupplierAdapter interface
    # ------------------------------------------------------------------

    def launch_session(self, request: LaunchRequest) -> LaunchResult:
        """Delegate to :meth:`_do_launch` with logging and timing."""
        correlation_id = str(uuid.uuid4())
        self._logger.info(
            "launch_session.start",
            extra={
                "supplier": self.supplier_id,
                "game_id": request.game_id,
                "player_id": request.player.player_id,
                "mode": request.mode.value,
                "correlation_id": correlation_id,
            },
        )
        start = time.monotonic()
        try:
            result = self._do_launch(request, correlation_id)
            elapsed = time.monotonic() - start
            self._logger.info(
                "launch_session.ok",
                extra={
                    "supplier": self.supplier_id,
                    "session_id": result.session_id,
                    "elapsed_ms": round(elapsed * 1000, 2),
                    "correlation_id": correlation_id,
                },
            )
            return result
        except GameServiceError:
            raise
        except Exception as exc:
            elapsed = time.monotonic() - start
            self._logger.error(
                "launch_session.error",
                extra={
                    "supplier": self.supplier_id,
                    "error": str(exc),
                    "elapsed_ms": round(elapsed * 1000, 2),
                    "correlation_id": correlation_id,
                },
            )
            raise self._translate_error(exc, correlation_id) from exc

    def get_balance(self, session_id: str) -> WalletSnapshot:
        """Delegate to :meth:`_do_get_balance` with logging."""
        self._logger.debug(
            "get_balance.start",
            extra={"supplier": self.supplier_id, "session_id": session_id},
        )
        try:
            return self._do_get_balance(session_id)
        except GameServiceError:
            raise
        except Exception as exc:
            raise self._translate_error(exc) from exc

    def debit(
        self,
        session_id: str,
        round_id: str,
        amount: Decimal,
        command: RoundCommand,
    ) -> TransactionResult:
        """Delegate to :meth:`_do_debit` with logging and idempotency hint."""
        self._logger.info(
            "debit.start",
            extra={
                "supplier": self.supplier_id,
                "session_id": session_id,
                "round_id": round_id,
                "amount": str(amount),
                "supplier_ref": command.supplier_ref,
            },
        )
        try:
            result = self._do_debit(session_id, round_id, amount, command)
            self._logger.info(
                "debit.ok",
                extra={
                    "supplier": self.supplier_id,
                    "transaction_id": result.transaction_id,
                    "already_processed": result.already_processed,
                },
            )
            return result
        except GameServiceError:
            raise
        except Exception as exc:
            self._logger.error(
                "debit.error",
                extra={"supplier": self.supplier_id, "error": str(exc)},
            )
            raise self._translate_error(exc) from exc

    def credit(
        self,
        session_id: str,
        round_id: str,
        amount: Decimal,
        command: RoundCommand,
    ) -> TransactionResult:
        """Delegate to :meth:`_do_credit` with logging."""
        self._logger.info(
            "credit.start",
            extra={
                "supplier": self.supplier_id,
                "session_id": session_id,
                "round_id": round_id,
                "amount": str(amount),
            },
        )
        try:
            result = self._do_credit(session_id, round_id, amount, command)
            self._logger.info(
                "credit.ok",
                extra={
                    "supplier": self.supplier_id,
                    "transaction_id": result.transaction_id,
                },
            )
            return result
        except GameServiceError:
            raise
        except Exception as exc:
            raise self._translate_error(exc) from exc

    def rollback(
        self,
        session_id: str,
        round_id: str,
        original_ref: str,
    ) -> TransactionResult:
        """Delegate to :meth:`_do_rollback` with logging."""
        self._logger.warning(
            "rollback.start",
            extra={
                "supplier": self.supplier_id,
                "session_id": session_id,
                "round_id": round_id,
                "original_ref": original_ref,
            },
        )
        try:
            result = self._do_rollback(session_id, round_id, original_ref)
            self._logger.warning(
                "rollback.ok",
                extra={
                    "supplier": self.supplier_id,
                    "transaction_id": result.transaction_id,
                },
            )
            return result
        except GameServiceError:
            raise
        except Exception as exc:
            raise self._translate_error(exc) from exc

    def end_session(self, session_id: str) -> None:
        """Delegate to :meth:`_do_end_session` with logging."""
        self._logger.info(
            "end_session.start",
            extra={"supplier": self.supplier_id, "session_id": session_id},
        )
        try:
            self._do_end_session(session_id)
            self._logger.info(
                "end_session.ok",
                extra={"supplier": self.supplier_id, "session_id": session_id},
            )
        except GameServiceError:
            raise
        except Exception as exc:
            raise self._translate_error(exc) from exc

    # ------------------------------------------------------------------
    # Template methods — override in subclasses
    # ------------------------------------------------------------------

    def _do_launch(
        self, request: LaunchRequest, correlation_id: str
    ) -> LaunchResult:  # pragma: no cover
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement _do_launch"
        )

    def _do_get_balance(self, session_id: str) -> WalletSnapshot:  # pragma: no cover
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement _do_get_balance"
        )

    def _do_debit(
        self,
        session_id: str,
        round_id: str,
        amount: Decimal,
        command: RoundCommand,
    ) -> TransactionResult:  # pragma: no cover
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement _do_debit"
        )

    def _do_credit(
        self,
        session_id: str,
        round_id: str,
        amount: Decimal,
        command: RoundCommand,
    ) -> TransactionResult:  # pragma: no cover
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement _do_credit"
        )

    def _do_rollback(
        self,
        session_id: str,
        round_id: str,
        original_ref: str,
    ) -> TransactionResult:  # pragma: no cover
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement _do_rollback"
        )

    def _do_end_session(self, session_id: str) -> None:  # pragma: no cover
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement _do_end_session"
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _translate_error(
        self,
        exc: Exception,
        correlation_id: str | None = None,
    ) -> GameServiceError:
        """Convert an unexpected exception into a :class:`GameServiceError`.

        Subclasses should override this to map supplier-specific HTTP status
        codes or error payloads to the appropriate domain exception subclass.
        """
        return GameServiceError(
            message=f"Unexpected error from supplier {self.supplier_id!r}: {exc}",
            retriable=True,
            correlation_id=correlation_id,
        )

    @staticmethod
    def _new_transaction_id() -> str:
        """Generate a new platform transaction UUID."""
        return str(uuid.uuid4())

    @staticmethod
    def _utcnow() -> datetime:
        """Return timezone-aware UTC datetime."""
        return datetime.now(tz=timezone.utc)


__all__ = [
    "BaseSupplierAdapter",
    "LaunchResult",
    "SupplierAdapter",
    "SupplierCapabilities",
    "SupplierInfo",
    "TransactionResult",
]

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
accounts_provider.py
--------------------
Protocol (interface) that every supplier integration must implement.

The Game Aggregation Layer (GAL) routes every transaction through an
AccountsProvider. The platform ships a StandardAccountsProvider (in-house
wallet) and one provider per external supplier. The correct provider is
selected at runtime via the supplier registry.

Design notes
------------
* All methods are async — supplier HTTP calls are I/O-bound.
* Amounts are in *minor units* (pence, cents) as integers. The only
  exception is when a supplier API requires a decimal value, in which
  case the provider is responsible for the conversion.
* Each provider is stateless; session state lives in the player's token
  or in the platform database.
"""

from __future__ import annotations

from abc import abstractmethod
from decimal import Decimal
from typing import Optional, Protocol, runtime_checkable

from transaction_result import BalanceStatus, TransactionResult, TransactionType


# ---------------------------------------------------------------------------
# Player session model
# ---------------------------------------------------------------------------


class PlayerSession:
    """
    Minimal player context passed between the bridge and providers.

    The bridge decodes the game token and produces a PlayerSession.
    Providers use the session to route requests to the correct wallet
    and to enforce session validity.
    """

    __slots__ = (
        "player_id",
        "brand_id",
        "external_id",
        "currency",
        "country",
        "jurisdiction",
        "session_token",
        "game_id",
        "mobile",
        "credentials",
    )

    def __init__(
        self,
        player_id: str,
        brand_id: str,
        external_id: str,
        currency: str,
        country: str,
        jurisdiction: str,
        session_token: str,
        game_id: str,
        mobile: bool = False,
        credentials: Optional[str] = None,
    ) -> None:
        self.player_id = player_id
        self.brand_id = brand_id
        self.external_id = external_id
        self.currency = currency
        self.country = country
        self.jurisdiction = jurisdiction
        self.session_token = session_token
        self.game_id = game_id
        self.mobile = mobile
        self.credentials = credentials

    def __repr__(self) -> str:
        return (
            f"PlayerSession(player_id={self.player_id!r}, "
            f"brand_id={self.brand_id!r}, "
            f"currency={self.currency!r})"
        )


# ---------------------------------------------------------------------------
# Operation descriptors (mirror Scala's SupplierAccountsOperation)
# ---------------------------------------------------------------------------


class SupplierOperation:
    """Base descriptor for a single wallet operation within a transaction."""

    __slots__ = ("round_id",)

    def __init__(self, round_id: str) -> None:
        self.round_id = round_id


class DebitOperation(SupplierOperation):
    """Take stake from the player's wallet."""

    __slots__ = ("amount", "error_if_using_bonus", "apply_wagering")

    def __init__(
        self,
        round_id: str,
        amount: Decimal,
        error_if_using_bonus: Optional[str] = None,
        apply_wagering: bool = True,
    ) -> None:
        super().__init__(round_id)
        self.amount = amount
        self.error_if_using_bonus = error_if_using_bonus
        self.apply_wagering = apply_wagering


class CreditOperation(SupplierOperation):
    """Return winnings to the player's wallet."""

    __slots__ = ("amount", "apply_geoverification")

    def __init__(
        self,
        round_id: str,
        amount: Decimal,
        apply_geoverification: bool = False,
    ) -> None:
        super().__init__(round_id)
        self.amount = amount
        self.apply_geoverification = apply_geoverification


class RefundOperation(SupplierOperation):
    """Reverse a previous debit (incomplete round rollback)."""

    __slots__ = ("original_tx_id",)

    def __init__(self, round_id: str, original_tx_id: str) -> None:
        super().__init__(round_id)
        self.original_tx_id = original_tx_id


class AdjustOperation(SupplierOperation):
    """Resettlement — adjust a previous credit upward."""

    __slots__ = ("new_amount", "apply_wagering")

    def __init__(self, round_id: str, new_amount: Decimal, apply_wagering: bool = False) -> None:
        super().__init__(round_id)
        self.new_amount = new_amount
        self.apply_wagering = apply_wagering


class ClawbackOperation(SupplierOperation):
    """Resettlement — claw back part of a previous credit."""

    __slots__ = ("amount",)

    def __init__(self, round_id: str, amount: Decimal) -> None:
        super().__init__(round_id)
        self.amount = amount


# ---------------------------------------------------------------------------
# Transaction context
# ---------------------------------------------------------------------------


class TransactionContext:
    """
    Metadata attached to every transaction call.

    Providers use this to enforce session validity, offline mode, and
    responsible-gambling controls.
    """

    __slots__ = (
        "tx_id",
        "supplier_ref",
        "disallow_locked",
        "reject_if_rc_elapsed",
        "offline",
        "allow_rollback_when_round_complete",
        "require_debits",
    )

    def __init__(
        self,
        tx_id: str,
        supplier_ref: str,
        disallow_locked: bool = False,
        reject_if_rc_elapsed: bool = False,
        offline: bool = False,
        allow_rollback_when_round_complete: bool = False,
        require_debits: bool = False,
    ) -> None:
        self.tx_id = tx_id
        self.supplier_ref = supplier_ref
        self.disallow_locked = disallow_locked
        self.reject_if_rc_elapsed = reject_if_rc_elapsed
        self.offline = offline
        self.allow_rollback_when_round_complete = allow_rollback_when_round_complete
        self.require_debits = require_debits


# ---------------------------------------------------------------------------
# Protocol definition
# ---------------------------------------------------------------------------


@runtime_checkable
class AccountsProvider(Protocol):
    """
    Contract that every wallet integration must satisfy.

    Implementations exist for:
    - StandardAccountsProvider  — in-house wallet (synchronous DB calls)
    - EvolutionProvider         — seamless-wallet callback
    - PragmaticProvider         — seamless-wallet callback
    - KambiProvider             — sportsbook fund/withdraw
    - … (one per supplier)

    The bridge selects the correct implementation based on the brand's
    configured supplier via the supplier registry.
    """

    @abstractmethod
    async def authenticate(self, token: str) -> PlayerSession:
        """
        Validate a game-launch token and return the player session.

        Raises:
            AuthenticationError: Token is invalid or expired.
            InvalidSessionError: Session state is not ACTIVE.
            UserLockedError: Account is locked (self-exclusion, fraud).
        """
        ...

    @abstractmethod
    async def get_balance(
        self, session: PlayerSession, game_id: Optional[str] = None
    ) -> BalanceStatus:
        """
        Retrieve the player's current wallet balance.

        The game_id is required when a game has an associated bonus that
        should be included in the playable balance.

        Raises:
            AuthenticationError: Session is no longer valid.
            AccountingError: Wallet system is unavailable.
        """
        ...

    @abstractmethod
    async def debit(
        self,
        session: PlayerSession,
        operation: DebitOperation,
        context: TransactionContext,
    ) -> TransactionResult:
        """
        Deduct a stake from the player's wallet.

        This is called when the player places a bet. The operation
        carries the round_id (for idempotency) and the amount.

        Raises:
            InsufficientFundsError: Balance too low.
            TransactionBlockedError: RG or compliance rule triggered.
            AccountLimitReachedError: Loss/deposit limit exceeded.
        """
        ...

    @abstractmethod
    async def credit(
        self,
        session: PlayerSession,
        operation: CreditOperation,
        context: TransactionContext,
    ) -> TransactionResult:
        """
        Add winnings to the player's wallet.

        Credits may arrive after the player session has ended (offline
        mode). Providers must handle this gracefully.
        """
        ...

    @abstractmethod
    async def refund(
        self,
        session: PlayerSession,
        operation: RefundOperation,
        context: TransactionContext,
    ) -> TransactionResult:
        """
        Reverse a previous debit for an incomplete round.

        Called when a round is abandoned before settlement. The provider
        must locate the original debit and reverse it.

        Raises:
            NoMatchingDebitError: Original debit not found.
        """
        ...

    @abstractmethod
    async def apply_transaction(
        self,
        session: PlayerSession,
        operations: list[SupplierOperation],
        context: TransactionContext,
    ) -> TransactionResult:
        """
        Apply a composite transaction (debit + credit in one round-trip).

        This is used by suppliers like Evolution that send a combined
        debit/credit as a single callback. The provider applies each
        operation atomically.
        """
        ...

    @abstractmethod
    async def reverse_transaction(
        self,
        session: PlayerSession,
        operations: list[SupplierOperation],
        context: TransactionContext,
    ) -> TransactionResult:
        """
        Reverse a previously applied composite transaction.

        Used during refund flow when the original transaction contained
        multiple operations.
        """
        ...

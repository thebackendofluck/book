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
gameservice.accounts_provider — AccountsProvider Protocol
==========================================================

Defines the structural contract that any wallet back-end must satisfy.
The platform never imports a concrete class; it depends only on this
Protocol, which enables:

* **Dependency inversion**: swap wallet providers without touching the core.
* **Testability**: pass any conforming mock without subclassing.
* **Multiple brands**: different brands can use different providers registered
  under the same interface.

Using ``typing.Protocol``
-------------------------
Python's ``Protocol`` (PEP 544) implements *structural subtyping*: a class
satisfies the protocol if it has the right methods with compatible signatures,
regardless of inheritance.  This is the idiomatic Python alternative to Java
interfaces or Scala traits.

Comparison with ABC
-------------------
``ABC`` forces inheritance; ``Protocol`` does not.  Third-party wallet
providers we do not own cannot be forced to extend our ``ABC``, but they
naturally conform to the ``Protocol`` if their method signatures match.

Example usage::

    from acmetocasino.gameservice.accounts_provider import AccountsProvider

    def process(provider: AccountsProvider, ...) -> None:
        balance = provider.get_balance(player_id="p-123", game_id="starburst")
        ...
"""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol, runtime_checkable

from acmetocasino.gameservice.models.enums import RealityCheckAction
from acmetocasino.gameservice.models.player_context import PlayerContext
from acmetocasino.gameservice.models.round_command import RoundCommand
from acmetocasino.gameservice.models.wallet_snapshot import WalletSnapshot
from acmetocasino.gameservice.transaction_result import TransactionResult


class AuthResult(Protocol):
    """The minimal interface expected from an authentication response.

    Concrete implementations may carry additional fields (JWT payload,
    session TTL, device fingerprint), but the platform core only relies
    on these two attributes.

    Attributes
    ----------
    player_id:
        The authenticated player's platform UUID.
    session_token:
        A fresh opaque token that identifies this session.  Passed back
        to the supplier for callback authentication.
    """

    player_id: str
    session_token: str


@runtime_checkable
class AccountsProvider(Protocol):
    """Structural contract for wallet / account back-ends.

    Any object that implements these methods with compatible signatures
    satisfies this protocol — no inheritance required.

    Thread safety
    -------------
    Implementations are expected to be thread-safe at the individual-method
    level.  Cross-method atomicity (e.g. check-then-act) is the caller's
    responsibility; :class:`~acmetocasino.gameservice.accounts_bridge.AccountsBridge`
    uses per-player locking to ensure round-trip consistency.
    """

    def authenticate(self, ctx: PlayerContext) -> AuthResult:
        """Validate the player's session and return their identity.

        Parameters
        ----------
        ctx:
            Player context including the session token to validate.

        Returns
        -------
        AuthResult
            Populated with the canonical player ID and a fresh session token.

        Raises
        ------
        ~acmetocasino.gameservice.errors.InvalidSessionError
            If the session token is expired, revoked, or unknown.
        ~acmetocasino.gameservice.errors.KycNotApprovedError
            If the jurisdiction requires KYC and the player has not completed it.
        """
        ...

    def get_balance(
        self,
        player_id: str,
        game_id: str | None = None,
    ) -> WalletSnapshot:
        """Return the player's current wallet balances.

        Parameters
        ----------
        player_id:
            Platform player UUID.
        game_id:
            Optional game scope for free-round credit balances.  Pass
            ``None`` to get the global wallet balance.

        Returns
        -------
        WalletSnapshot
            Point-in-time balance snapshot.
        """
        ...

    def apply_transaction(
        self,
        player_id: str,
        game_id: str,
        round_id: str,
        operations: list[RoundCommand],
    ) -> TransactionResult:
        """Apply a set of round commands atomically to the player's wallet.

        All commands in ``operations`` are applied in order as a single
        atomic unit — either all succeed or none are applied.

        Parameters
        ----------
        player_id:
            Target player.
        game_id:
            The game for which these commands are submitted.  Used to scope
            free-round credits and wagering-contribution lookups.
        round_id:
            The supplier's round identifier.  Used to detect duplicate
            submissions (idempotency).
        operations:
            Ordered list of :class:`~acmetocasino.gameservice.models.RoundCommand`
            objects to apply.

        Returns
        -------
        TransactionResult
            Outcome with post-transaction balance.

        Raises
        ------
        ~acmetocasino.gameservice.errors.InsufficientFundsError
            If a DEBIT would make the balance negative.
        ~acmetocasino.gameservice.errors.RoundClosedError
            If the round has already been settled or voided.
        """
        ...

    def reverse_transaction(
        self,
        player_id: str,
        round_id: str,
        operations: list[RoundCommand],
    ) -> TransactionResult:
        """Reverse previously-applied round commands (rollback).

        Typically called when a game round is interrupted and the supplier
        needs to cancel the outstanding debit.

        Parameters
        ----------
        player_id:
            Target player.
        round_id:
            The round whose commands should be reversed.
        operations:
            The ROLLBACK commands to apply.

        Returns
        -------
        TransactionResult
            Outcome with post-reversal balance.

        Raises
        ------
        ~acmetocasino.gameservice.errors.NoMatchingDebitError
            If no debit for ``round_id`` exists in the ledger.
        """
        ...

    def add_bonus(
        self,
        player_id: str,
        amount: Decimal,
        bonus_type: str,
    ) -> TransactionResult:
        """Credit a bonus award to the player's bonus balance.

        Parameters
        ----------
        player_id:
            Target player.
        amount:
            Bonus amount in the player's currency.
        bonus_type:
            Classifier string (e.g. ``"welcome"``, ``"reload"``, ``"free_round_win"``).

        Returns
        -------
        TransactionResult
            Outcome with post-credit balance.
        """
        ...

    def confirm_reality_check(
        self,
        player_id: str,
        action: RealityCheckAction,
    ) -> None:
        """Record the player's response to a reality-check prompt.

        This resets the elapsed-time counter so the player can continue
        playing (for ``CONTINUE``) or triggers session close (for
        ``TAKE_BREAK`` / ``SET_LIMIT``).

        Parameters
        ----------
        player_id:
            Target player.
        action:
            The player's chosen response.
        """
        ...


__all__ = ["AccountsProvider", "AuthResult"]

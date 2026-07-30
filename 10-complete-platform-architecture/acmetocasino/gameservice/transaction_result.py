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
gameservice.transaction_result — TransactionResult
===================================================

Every wallet operation — debit, credit, rollback, bonus award — returns a
:class:`TransactionResult`.  This single return type lets the caller always
inspect the same fields, regardless of what happened.

Design goals
------------
* **Informative on failure**: instead of raising for every possible error the
  caller has explicitly handled, the result carries ``succeeded=False`` with
  an ``error_message``.  Domain-breaking errors (e.g. network failure) still
  raise exceptions.
* **Idempotency signal**: ``already_processed=True`` tells the adapter layer
  that it received a duplicate callback and should return its cached response
  rather than applying the operation again.
* **Responsible-gambling hooks**: ``reality_check_elapsed`` lets the adapter
  embed a reality-check prompt in the game response without an additional API
  call.
* **Decimal-safe**: all monetary values are ``Decimal`` to avoid floating-
  point representation errors.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from acmetocasino.gameservice.models.wallet_snapshot import WalletSnapshot


class TransactionResult(BaseModel):
    """The outcome of a single wallet operation or a batch of round commands.

    Attributes
    ----------
    external_id:
        The platform's own ledger entry identifier for this transaction.
        Callers should persist this for auditing and dispute resolution.
    balance:
        The player's wallet state *after* this operation was applied (or
        after the existing result was retrieved for idempotent replays).
    cash_usage:
        How much of the transaction amount was drawn from the cash balance.
    bonus_usage:
        How much of the transaction amount was drawn from the bonus balance.
    reality_check_elapsed:
        ``True`` when the player has been in-session long enough to trigger
        a mandatory reality-check prompt.  The adapter must surface this to
        the supplier so it can display the dialog.
    already_processed:
        ``True`` when this call is a duplicate of a previously-processed
        transaction (identified by ``supplier_ref``).  The balance and usage
        figures reflect the *original* operation's outcome.
    succeeded:
        ``False`` for soft failures (e.g. insufficient funds) that were
        handled gracefully.  Hard failures raise exceptions instead.
    error_message:
        Human-readable description of why ``succeeded=False``.  ``None``
        when the operation succeeded.
    """

    external_id: str = Field(
        ...,
        description="Platform ledger entry ID for this transaction.",
    )
    balance: WalletSnapshot = Field(
        ...,
        description="Wallet state after this operation was applied.",
    )
    cash_usage: Decimal = Field(
        default=Decimal("0"),
        ge=Decimal("0"),
        description="Portion of the transaction amount drawn from cash.",
    )
    bonus_usage: Decimal = Field(
        default=Decimal("0"),
        ge=Decimal("0"),
        description="Portion of the transaction amount drawn from bonus.",
    )
    reality_check_elapsed: bool = Field(
        default=False,
        description="Whether the player's reality-check interval has been reached.",
    )
    already_processed: bool = Field(
        default=False,
        description="True when this is an idempotent replay of a prior operation.",
    )
    succeeded: bool = Field(
        default=True,
        description="False when the operation completed with a soft error.",
    )
    error_message: str | None = Field(
        default=None,
        description="Reason for failure when succeeded=False.",
    )

    @property
    def total_usage(self) -> Decimal:
        """Total funds moved (cash + bonus), regardless of source."""
        return self.cash_usage + self.bonus_usage

    def __repr__(self) -> str:
        status = "ok" if self.succeeded else f"FAILED({self.error_message!r})"
        return (
            f"TransactionResult(id={self.external_id!r}, "
            f"status={status}, "
            f"balance={self.balance.total_balance} {self.balance.currency})"
        )


__all__ = ["TransactionResult"]

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
gameservice.models.wallet_snapshot — Immutable Balance View
============================================================

A :class:`WalletSnapshot` represents the player's wallet state at a single
point in time.  It is returned by every wallet operation so the supplier
(and the player's UI) can immediately display an up-to-date balance without
an additional round-trip.

Key design decisions
--------------------
* All monetary values are ``Decimal`` — never ``float``.  Floating-point
  arithmetic is unsuitable for financial calculations due to representation
  errors.
* The model is *immutable* (``frozen=True``).  Wallet states should never be
  mutated in-place; create a new snapshot instead.
* ``total_balance`` is a computed property rather than a stored field, so it
  is always consistent with the component balances.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field, computed_field, model_validator


class WalletSnapshot(BaseModel):
    """An immutable point-in-time view of a player's wallet balances.

    Attributes
    ----------
    cash_balance:
        Real-money funds that are freely withdrawable.
    bonus_balance:
        Promotional funds subject to wagering requirements.
    free_round_credits:
        Credits awarded for specific free-round promotions.  These are
        typically only usable within a defined game/supplier scope.
    currency:
        ISO-4217 currency code (e.g. ``"EUR"``, ``"GBP"``, ``"BRL"``).
    snapshot_at:
        Optional ISO-8601 timestamp string set by the wallet service to
        indicate when this balance was read.
    """

    model_config = {"frozen": True}

    cash_balance: Decimal = Field(
        ...,
        ge=Decimal("0"),
        description="Real-money withdrawable balance (must be non-negative).",
    )
    bonus_balance: Decimal = Field(
        default=Decimal("0"),
        ge=Decimal("0"),
        description="Promotional balance subject to wagering requirements.",
    )
    free_round_credits: Decimal = Field(
        default=Decimal("0"),
        ge=Decimal("0"),
        description="Credits restricted to free-round use.",
    )
    currency: str = Field(
        ...,
        min_length=3,
        max_length=3,
        description="ISO-4217 currency code.",
    )
    snapshot_at: str | None = Field(
        default=None,
        description="ISO-8601 UTC timestamp when this snapshot was taken.",
    )

    @computed_field  # type: ignore[misc]
    @property
    def total_balance(self) -> Decimal:
        """Sum of all balance components.

        This is what is typically shown to the player in the game HUD.
        """
        return self.cash_balance + self.bonus_balance + self.free_round_credits

    @model_validator(mode="after")
    def _validate_non_negative_total(self) -> WalletSnapshot:
        if self.total_balance < Decimal("0"):
            raise ValueError(
                f"Total wallet balance cannot be negative: {self.total_balance}"
            )
        return self

    def with_cash_delta(self, delta: Decimal) -> WalletSnapshot:
        """Return a new snapshot with ``cash_balance`` adjusted by ``delta``.

        Useful in unit tests and in-memory wallet implementations to create
        successive states without mutating the original.

        Raises
        ------
        ValueError
            If the resulting cash balance would be negative.
        """
        new_cash = self.cash_balance + delta
        if new_cash < Decimal("0"):
            raise ValueError(
                f"Cash balance would become negative: {new_cash} "
                f"(current={self.cash_balance}, delta={delta})"
            )
        return self.model_copy(update={"cash_balance": new_cash})

    def with_bonus_delta(self, delta: Decimal) -> WalletSnapshot:
        """Return a new snapshot with ``bonus_balance`` adjusted by ``delta``."""
        new_bonus = self.bonus_balance + delta
        if new_bonus < Decimal("0"):
            raise ValueError(
                f"Bonus balance would become negative: {new_bonus}"
            )
        return self.model_copy(update={"bonus_balance": new_bonus})

    def __repr__(self) -> str:
        return (
            f"WalletSnapshot(cash={self.cash_balance}, "
            f"bonus={self.bonus_balance}, "
            f"total={self.total_balance} {self.currency})"
        )


__all__ = ["WalletSnapshot"]
